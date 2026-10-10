"""Shared post-processing for LLM formulation recommendations."""
from __future__ import annotations

import logging
import uuid
from collections import defaultdict, deque
from typing import NamedTuple

from ..config import Settings, get_settings
from ..domain.schemas import Formulation, RecommendedFormula
from ..domain.tradeoff_schemas import TradeOffAnalysis
from . import chemtools
from .recommend_diversity import select_diverse_mmr
from .tradeoff_analysis import analyze_tradeoffs

logger = logging.getLogger(__name__)


class RecommendBundle(NamedTuple):
    """Result of :func:`run_recommend_orchestration`.

    ``warnings`` merges every stage's warnings (LLM synthesis + grounding +
    gates) so callers can no longer drop one on the floor.
    """

    aligned_formulas: list[RecommendedFormula]
    scored: list[Formulation]
    warnings: list[str]
    tradeoff: TradeOffAnalysis | None
    requested_n: int
    diversity_applied: bool
    engine: str
    # C-8: stable id for one recommendation round — returned to the client
    # in the API response and used to correlate adopt-outcome telemetry.
    recommend_id: str = ""


def run_recommend_orchestration(
    req,
    evidence: list,
    *,
    requested_n: int | None = None,
    objectives=None,
    include_tradeoff: bool = True,
    scenario_kinds=None,
    prefer_materials_catalog: bool = False,
    modify_prompt: str = "",
    base_formulas=None,
    synth_override=None,
    # P1-1: llm_only/无证据时关闭 strict grounding（tag-only，不剔除）
    strict_grounding: bool = True,
    settings: Settings | None = None,
    # 增量 #11: A/B 分流键（用户 ID）。None = 不分流（对照组）。
    # 按用户 ID 哈希保证同一用户体验一致。
    ab_user_id: str | None = None,
) -> RecommendBundle:
    """Single orchestration entry for formulation recommendation.

    Encapsulates the previously triplicated sequence shared by
    ``api/formulations.py::recommend_formulations``,
    ``research_graph.recommend_generate_node`` and
    ``research_graph.generate_node``:

    1. ``llm.recommend_formulations`` (LLM synthesis, offline fallback inside)
    2. ``ground_recommended_formulas`` (grounding against evidence)
    3. ``finalize_recommendation_bundle`` (score / dedupe / diversify / trade-off)

    Retrieval (Step 0) stays with the caller — the API path has its own
    hybrid/kb_only/llm_only mode handling and passes its synthesized response
    via ``synth_override`` (already carrying its mode-specific fallbacks).
    Response shaping stays with the caller — each entry keeps its own schema.
    """
    from ..services.grounded_recommend import ground_recommended_formulas

    settings = settings or get_settings()
    n = resolve_recommend_n(requested_n, settings=settings)
    # C-8: one id per recommendation round, shared by API response + telemetry.
    recommend_id = uuid.uuid4().hex

    # 增量 #11: A/B 分流 —— 按用户 ID 哈希，实验组用不同 MMR 参数。
    # 默认关闭（ab_user_id=None → 对照组）。
    import hashlib
    ab_group = "control"
    if ab_user_id and getattr(settings, "recommend_ab_enabled", False):
        _h = int(hashlib.md5(ab_user_id.encode()).hexdigest()[:8], 16)
        ab_group = "experiment" if _h % 2 == 0 else "control"
        logger.info("recommend A/B: user %s → %s (recommend_id=%s)",
                    ab_user_id[:8], ab_group, recommend_id[:8])

    if synth_override is not None:
        rec_resp = synth_override
    else:
        from ..services import llm

        llm_n = llm_candidate_count(n, settings=settings)
        rec_resp = llm.recommend_formulations(
            req,
            objectives,
            evidence,
            n=llm_n,
            modify_prompt=modify_prompt,
            base_formulas=base_formulas,
        )
    warnings: list[str] = list(rec_resp.warnings)

    grounded_formulas, ground_warnings = ground_recommended_formulas(
        rec_resp.formulas,
        evidence,
        prefer_materials_catalog=prefer_materials_catalog,
        # P1-1: 无证据时 strict 会误删所有成分（含"水"），改用 tag-only
        strict=strict_grounding and bool(evidence),
    )
    warnings.extend(ground_warnings)

    aligned, scored, gate_warnings, _, diversity_applied, tradeoff = (
        finalize_recommendation_bundle(
            grounded_formulas,
            req,
            evidence,
            requested_n=n,
            objectives=objectives,
            include_tradeoff=include_tradeoff,
            scenario_kinds=scenario_kinds,
            settings=settings,
        )
    )
    warnings.extend(gate_warnings)

    return RecommendBundle(
        aligned_formulas=aligned,
        scored=scored,
        warnings=warnings,
        tradeoff=tradeoff,
        requested_n=n,
        diversity_applied=diversity_applied,
        engine=rec_resp.engine,
        recommend_id=recommend_id,
    )


def resolve_recommend_n(requested: int | None, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    n = requested if requested is not None else settings.recommend_default_n
    return max(1, min(int(n), settings.recommend_max_n))


def llm_candidate_count(requested_n: int, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    if settings.recommend_diversity_enabled and requested_n > 1:
        return min(requested_n * 2, settings.recommend_max_n)
    return requested_n


def finalize_scored_formulations(
    rec_formulas: list[RecommendedFormula],
    scored: list[Formulation],
    *,
    n: int,
    settings: Settings | None = None,
) -> tuple[list[Formulation], list[RecommendedFormula], list[str], bool]:
    settings = settings or get_settings()
    scored.sort(key=lambda f: (f.score or 0.0), reverse=True)
    scored, dedup_notes = chemtools.dedupe_similar_formulations(scored)

    diversity_applied = False
    if settings.recommend_diversity_enabled and len(scored) > n:
        scored, diversity_applied = select_diverse_mmr(
            scored,
            n,
            lambda_score=settings.recommend_diversity_lambda,
        )
    else:
        scored = scored[:n]

    name_to_recs: dict[str, deque] = defaultdict(deque)
    for r in rec_formulas:
        name_to_recs[r.name].append(r)
    formulas = []
    for f in scored:
        # v16 P3-12: 优先位置配对（_src_idx 随对象携带），回退名称配对。
        _si = getattr(f, "_src_idx", None)
        rec = None
        if _si is not None and 0 <= _si < len(rec_formulas):
            rec = rec_formulas[_si]
        else:
            queue = name_to_recs.get(f.name)
            if queue:
                rec = queue.popleft()
        if rec is not None:
            # v27 P1-9: 编排算出的 score/predicted 回填到响应 formulas。
            # 此前直接返回原始 RecommendedFormula，score=None、predicted={}
            #（v20 双字段展示契约未兑现）。
            rec.score = f.score
            rec.predicted = dict(f.predicted or {})
            formulas.append(rec)

    # v22: 配对完整性检查（P2-1: assert 改显式检查，生产环境 assert 可能被 -O 剥离）
    if len(formulas) != len(scored):
        logger.error(
            "recommendation pairing broken: %d != %d, truncating to shortest",
            len(formulas), len(scored),
        )
        _n = min(len(formulas), len(scored))
        formulas, scored = formulas[:_n], scored[:_n]
    return scored, formulas, dedup_notes, diversity_applied


def _rescore_with_shared_bounds(scored: list[Formulation], objectives, process) -> None:
    """Re-score a batch whose scores are normalised against one shared range.

    That is every batch except a single *maximize* objective (whose score is the raw
    predicted value): two or more objectives, or one that minimizes / matches a target.

    ``_score_and_validate`` scores each candidate on its own, normalising every
    metric without a user-given range against ``(0, 2 × that candidate's own
    value)`` — so a weak and a strong candidate both land on exactly 0.5 and the
    ranking below (``finalize_scored_formulations`` sorts by ``score``) is just
    the LLM's output order. Candidates are only comparable on a common ruler,
    so recompute the aggregate with :func:`predictor.shared_bounds`.

    The KG compatibility adjustment was applied to ``score`` as a multiplier
    after the first scoring; it is carried over as ``score / first_base_score``
    rather than recomputed. Fail-open: on any error the per-candidate scores
    stay as they were.
    """
    import math

    from . import predictor

    if not scored or not objectives or predictor.score_is_raw(objectives):
        return
    try:
        objectives = list(objectives)
        # v23-fix: 用 f.predicted（已含 metric_priors/bias 修正），与展示同源。
        props = [dict(f.predicted) if f.predicted else predictor.predict(f, process) for f in scored]
        shared = predictor.shared_bounds(objectives, props)
        rescored: list[float] = []
        for form, p in zip(scored, props):
            old_base = predictor.multi_objective_score(
                form, objectives, process, predictor.default_bounds(objectives, form), props=p
            )
            new_base = predictor.multi_objective_score(
                form, objectives, process, shared, props=p
            )
            factor = 1.0
            if form.score is not None and old_base > 1e-9:
                factor = float(form.score) / old_base
            value = float(new_base) * factor
            if not math.isfinite(value):
                raise ValueError(f"non-finite rescored value for {form.name!r}")
            rescored.append(value)
        for form, value in zip(scored, rescored):
            form.score = value
    except Exception:
        logger.warning("shared-bounds rescoring failed; keeping per-candidate scores", exc_info=True)


# Public name: substitution and inverse design rank their own batches on the same shared ruler.
rescore_with_shared_bounds = _rescore_with_shared_bounds


def finalize_recommendation_bundle(
    rec_formulas: list[RecommendedFormula],
    req,
    evidence: list,
    *,
    requested_n: int | None = None,
    objectives=None,
    include_tradeoff: bool = True,
    scenario_kinds=None,
    settings: Settings | None = None,
) -> tuple[
    list[RecommendedFormula],
    list[Formulation],
    list[str],
    int,
    bool,
    TradeOffAnalysis | None,
]:
    """Score, dedupe, diversify, and optionally analyze trade-offs."""
    from ..domain.formulation_gate import recommended_to_formulation, validate_formulations
    from ..domain.objective_contract import normalize_objectives
    from ..pipeline.claim_checker import check_formulation_predictions
    from ..pipeline.workflow import _score_and_validate, process_for

    settings = settings or get_settings()
    n = resolve_recommend_n(requested_n, settings=settings)
    objectives = objectives or normalize_objectives(req)
    process = process_for(req)
    warnings: list[str] = []
    scored: list[Formulation] = []

    for _ri, rec in enumerate(rec_formulas):
        try:
            form = recommended_to_formulation(rec)
            # ``objectives`` is the list trade-off analysis and the LLM prompt
            # use; scoring must rank by the same one (see _score_and_validate).
            scored_form = _score_and_validate(
                form, process, req, chem_screen=True, objectives=objectives
            )
            # v16 P3-12: 位置配对 —— 把原始下标挂在对象上，随排序/dedupe/MMR 携带。
            try:
                object.__setattr__(scored_form, "_src_idx", _ri)
            except Exception:  # noqa: BLE001
                pass
            scored.append(scored_form)
        except ValueError as exc:
            warnings.append(str(exc))
        except Exception as exc:  # noqa: BLE001
            # P1-3: 单个坏候选不拖死整轮 —— predictor/网络 enrich 等非 ValueError 异常跳过该候选
            logger.warning("recommend candidate %d skipped: %s", _ri, exc)
            warnings.append(f"候选 {_ri} 处理失败已跳过: {type(exc).__name__}")

    scored, gate_warnings = validate_formulations(scored, req=req)
    warnings.extend(gate_warnings)
    _rescore_with_shared_bounds(scored, objectives, process)
    for form in scored:
        warnings.extend(check_formulation_predictions(form, evidence))

    scored, aligned_formulas, dedup_notes, diversity_applied = finalize_scored_formulations(
        rec_formulas,
        scored,
        n=n,
        settings=settings,
    )
    warnings.extend(dedup_notes)

    tradeoff = None
    if include_tradeoff and settings.recommend_tradeoff_enabled:
        rec_map = {r.name: r for r in aligned_formulas}
        tradeoff = analyze_tradeoffs(
            scored,
            objectives,
            rec_map,
            scenario_kinds=scenario_kinds,
            req=req,
            settings=settings,
        )

    return aligned_formulas, scored, warnings, n, diversity_applied, tradeoff

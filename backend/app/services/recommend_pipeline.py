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

    import hashlib

    # N-4: 提示词模板 A/B —— 按 recommend_id 哈希分流，跟踪接地通过率
    prompt_variant = "v1"
    if settings.prompt_ab_enabled:
        _ph = int(hashlib.md5(recommend_id.encode()).hexdigest()[:8], 16)
        prompt_variant = "v2" if _ph % 2 == 0 else "v1"
        logger.info("recommend prompt A/B: %s → %s", recommend_id[:8], prompt_variant)

    if synth_override is not None:
        rec_resp = synth_override
    else:
        from ..services import llm

        llm_n = llm_candidate_count(n, settings=settings)
        # N-4: v2 变体在 modify_prompt 前加约束强调
        _mp = modify_prompt
        if prompt_variant == "v2":
            _mp = ("【重要】每个成分必须给出 CAS 号或分子式，配比总和必须为 100%。\n" + _mp) if _mp else "【重要】每个成分必须给出 CAS 号或分子式，配比总和必须为 100%。"
        rec_resp = llm.recommend_formulations(
            req,
            objectives,
            evidence,
            n=llm_n,
            modify_prompt=_mp,
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

    # O-1: A/B 追踪 —— 记录 prompt 版本和接地通过率
    try:
        from .recommend_ab_track import track_recommendation
        # 计算接地通过率（high grounding 的成分比例）
        total_comps = sum(len(getattr(f, "components", []) or []) for f in grounded_formulas)
        high_grounded = sum(
            1 for f in grounded_formulas
            for c in (getattr(f, "components", []) or [])
            if getattr(c, "grounding_confidence", "") == "high"
        )
        grounding_rate = (high_grounded / total_comps) if total_comps > 0 else 0
        track_recommendation(
            recommend_id=recommend_id,
            prompt_version=prompt_variant,
            consensus_enabled=settings.recommend_consensus_enabled,
            grounding_rate=grounding_rate,
            n_candidates=len(grounded_formulas),
        )
    except Exception:
        pass

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


def _complete_ratios(form) -> None:
    """N-5: 配比智能补全。缺失 weight_pct 时按成分类型默认分布补全。

    规则（涂料典型配比）：
    - 树脂/基料：40-60%
    - 颜料/填料：20-40%
    - 溶剂：10-30%
    - 助剂：0.5-5%
    缺失时按类型分配，剩余按比例归一化到 100%。
    """
    ingredients = getattr(form, "ingredients", None) or getattr(form, "components", []) or []
    if not ingredients:
        return
    # 类型关键词 → 默认配比
    _defaults = [
        (("resin", "树脂", "基料", "epoxy", "环氧"), 50.0),
        (("pigment", "颜料", "tio2", "钛白"), 25.0),
        (("filler", "填料", "碳酸钙", "滑石"), 15.0),
        (("solvent", "溶剂", "二甲苯", "丁醇"), 15.0),
        (("additive", "助剂", "分散剂", "消泡剂", "流平剂"), 2.0),
        (("hardener", "固化剂", "amine", "胺"), 10.0),
    ]
    total_given = 0.0
    missing = []
    for ing in ingredients:
        wp = getattr(ing, "weight_pct", None)
        if wp and wp > 0:
            total_given += wp
        else:
            missing.append(ing)
    if not missing:
        return
    # 为缺失的分配默认值
    for ing in missing:
        name = (getattr(ing, "name", "") or "").lower()
        default = 5.0  # 兜底
        for keywords, pct in _defaults:
            if any(kw in name for kw in keywords):
                default = pct
                break
        try:
            ing.weight_pct = default
        except Exception:
            pass
        total_given += default
    # 归一化到 100
    if total_given > 0:
        factor = 100.0 / total_given
        for ing in ingredients:
            try:
                wp = getattr(ing, "weight_pct", 0) or 0
                ing.weight_pct = round(wp * factor, 2)
            except Exception:
                pass


def _score_ratio_reasonableness(form) -> list[str]:
    """O-4: 配比合理性评分。异常配比警告。

    规则（涂料典型范围）：
    - 树脂 30-70%，颜料 5-40%，溶剂 5-40%，助剂 0.1-10%，固化剂 5-30%
    - 单一成分 >80% 或 <0.1% 时警告
    """
    warnings = []
    ingredients = getattr(form, "ingredients", None) or getattr(form, "components", []) or []
    if not ingredients:
        return warnings
    _ranges = [
        (("resin", "树脂", "基料", "epoxy", "环氧"), 30.0, 70.0),
        (("pigment", "颜料", "tio2", "钛白"), 5.0, 40.0),
        (("filler", "填料", "碳酸钙"), 5.0, 40.0),
        (("solvent", "溶剂", "二甲苯"), 5.0, 40.0),
        (("additive", "助剂", "分散剂", "消泡剂"), 0.1, 10.0),
        (("hardener", "固化剂", "amine"), 5.0, 30.0),
    ]
    for ing in ingredients:
        name = (getattr(ing, "name", "") or "").lower()
        wp = getattr(ing, "weight_pct", None) or 0
        if wp <= 0:
            continue
        # 极端值检查
        if wp > 80:
            warnings.append(f"配比异常：{getattr(ing, 'name', '')} 占比 {wp:.1f}% 过高")
            continue
        if wp < 0.1:
            warnings.append(f"配比异常：{getattr(ing, 'name', '')} 占比 {wp:.2f}% 过低")
            continue
        # 类型范围检查
        for keywords, lo, hi in _ranges:
            if any(kw in name for kw in keywords):
                if not (lo <= wp <= hi):
                    warnings.append(
                        f"配比偏离典型范围：{getattr(ing, 'name', '')} {wp:.1f}%"
                        f"（典型 {lo:.0f}-{hi:.0f}%）"
                    )
                break
    return warnings


def resolve_recommend_n(requested: int | None, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    n = requested if requested is not None else settings.recommend_default_n
    return max(1, min(int(n), settings.recommend_max_n))


def llm_candidate_count(requested_n: int, *, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    # N-1: 多候选共识 —— 生成 3N 候选，聚类后选中心（需 diversity 启用）
    if settings.recommend_consensus_enabled and requested_n > 1:
        return min(requested_n * 3, settings.recommend_max_n)
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

    # N-1: 多候选共识 —— 3N 候选中按成分聚类，选每类最高分（减少 LLM 方差）
    if settings.recommend_consensus_enabled and len(scored) > n:
        try:
            from .recommend_diversity import formulation_similarity
            # 简单聚类：按相似度阈值分组
            clusters: list[list] = []
            for f in scored:
                placed = False
                for cl in clusters:
                    if formulation_similarity(f, cl[0]) > 0.8:
                        cl.append(f)
                        placed = True
                        break
                if not placed:
                    clusters.append([f])
            # 每类取最高分
            scored = sorted(
                [max(cl, key=lambda x: x.score or 0.0) for cl in clusters],
                key=lambda x: x.score or 0.0, reverse=True,
            )
            dedup_notes.append(f"N-1 共识：{len(clusters)} 簇中优选")
        except Exception:
            pass

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
    # N-2/N-3: 化学有效性校验 + 兼容性检查（fail-open）
    if settings.chem_validate_enabled:
        try:
            from .chem_validate import validate_formula_chemistry
            for form in scored:
                warnings.extend(validate_formula_chemistry(form))
        except Exception:
            pass
    # N-5: 配比智能补全 —— 缺失配比时按成分类型默认分布补全
    if settings.ratio_completion_enabled:
        try:
            for form in scored:
                _complete_ratios(form)
        except Exception:
            pass
    # O-4: 配比合理性评分 —— 异常配比警告
    try:
        for form in scored:
            warnings.extend(_score_ratio_reasonableness(form))
    except Exception:
        pass

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

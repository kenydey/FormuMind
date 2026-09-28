"""Phase 2 A/B：retrieval_by_children 默认开关决策（golden 集 coverage）。

方法（确定性、可复现）：
- 以 golden_rigor 32 组为语料：每条 evidence 变成一个 parent，并在其前后
  追加"稀释句"——含通用查询词（配方/性能/研究）但不含答案关键句的句子。
  这模拟真实长 chunk：答案句被稀释。
- parent-only：整块关键词 overlap 打分（现状）。
- children：children_rerank（子块打分、父块呈现）。
- 指标：key_claim coverage（沿用 test_agent_search_loop 的 _coverage 定义）。

决策规则（与 Wave 1 同口径）：
- children_cov >= parent_cov - 0.01 → 不退化；
- children_cov > parent_cov + 0.05 → 建议默认开启。
结论写死在测试 docstring，开关翻转需同步更新此处。
"""
import re

from app.domain.schemas import Evidence
from app.services.children_retrieval import children_rerank
from app.services.hybrid_search import ScoredChunk
from app.services.rag import _bm25_tokenize


def _norm(text: str) -> str:
    t = (text or "").lower()
    return re.sub(r"[\s\u3000\-–—_.,;:!?，。；：！？、（）()\[\]【】\"'“”‘’·/\\]+", "", t)


def _ev(ident, title="", snippet=""):
    return Evidence(source="kb", identifier=ident, title=title,
                    snippet=snippet, relevance=0.5)


def _coverage(pair, retrieved) -> float:
    texts = [_norm(ev.title + " " + ev.snippet) for ev in retrieved]
    claims = pair.get("key_claims") or []
    if not claims:
        return 1.0
    hit = 0
    for c in claims:
        kws = c.get("keywords") or []
        m = sum(1 for kw in kws if any(_norm(kw) in t for t in texts))
        if kws and m / len(kws) >= 0.5:
            hit += 1
    return hit / len(claims)


_DISTRACTORS = (
    "配方研究是材料科学的重要方向。",
    "性能测试需要严格控制实验条件。",
    "相关研究表明工艺参数影响显著。",
    "本领域技术人员可以进行常规优化。",
)


def _diluted_parent(ev_text: str) -> str:
    return " ".join(_DISTRACTORS[:2]) + " " + ev_text + " " + " ".join(_DISTRACTORS[2:])


def _parent_score(query: str, parent_text: str) -> float:
    qt = {_norm(w) for w in _bm25_tokenize(query) if len(w.strip()) >= 2}
    dt = {_norm(w) for w in _bm25_tokenize(parent_text) if len(w.strip()) >= 2}
    return len(qt & dt) / max(1, len(qt))


class _FakeChunk:
    def __init__(self, text):
        self.text = text


def _run_ab():
    from app.resources.golden_rigor import golden_rigor_pairs

    tot_parent = tot_child = 0.0
    n = 0
    for pair in golden_rigor_pairs:
        q = pair["question"]
        parents = []
        for j, ev in enumerate(pair.get("evidence") or []):
            text = ev.get("text") or ""
            if not text.strip():
                continue
            parents.append((f"g{n}-{j}", _diluted_parent(text)))
        if not parents:
            continue
        n += 1
        # parent-only: 整块打分取 top-6
        ranked = sorted(parents, key=lambda p: -_parent_score(q, p[1]))
        parent_ret = [_ev(i, snippet=t) for i, t in ranked[:6]]
        tot_parent += _coverage(pair, parent_ret)
        # children: 子块打分、父块呈现
        scored = [
            ScoredChunk(chunk=_FakeChunk(t), bm25_score=s, cosine_score=s,
                        hybrid_score=s)
            for i, t in parents for s in [_parent_score(q, t)]
        ]
        reranked = children_rerank(q, scored, candidate_mult=3, top_k=6)
        child_ret = [_ev(f"kb:x#{k}", snippet=s.chunk.text)
                     for k, s in enumerate(reranked)]
        tot_child += _coverage(pair, child_ret)
    return (tot_parent / n if n else 0.0, tot_child / n if n else 0.0, n)


def test_ab_children_vs_parent_golden():
    """A/B 结论（2026-09-29 实测）：parent_cov == children_cov == 1.000（n=44），
    加硬干扰句（干扰句含查询关键词）后仍是 1.000 vs 1.000。

    阴性结论：golden 语料每对证据池小、top-6 恒覆盖，合成 A/B 无法区分；
    真实长 chunk 稀释场景下 children 的理论优势未被证实也未被证伪。
    按决策矩阵默认保持关闭（kb_children_retrieval_enabled=False），opt-in 保留。
    若未来有长 chunk 语料证明 children_cov > parent_cov + 0.05，可翻转开关并
    同步更新本 docstring。
    """
    parent_cov, child_cov, n = _run_ab()
    print(f"\nA/B: n={n} parent_cov={parent_cov:.3f} children_cov={child_cov:.3f}")
    # 永不退化是硬要求
    assert child_cov >= parent_cov - 0.01, (
        f"children 回归: parent={parent_cov:.3f} children={child_cov:.3f}"
    )

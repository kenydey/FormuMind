"""主题预筛(永久规则 2026-09)测试 — kb_ingest 自动入库前的领域相关性过滤。

背景: 2026-09-04 一次自动入库吞进 29 篇与"含聚合物/树脂的乳液型镁合金
钝化剂"无关的文献(Heusler 磁性/储氢/电池/天文/纯力学)。本测试固定规则:
高判别词≥1 或低判别词≥2 放行; 反向硬拦词命中且高判别词<2 拦截;
专利豁免; 项目无领域锚词不过滤; config 可整体关闭。
"""
from __future__ import annotations

import pytest

from app.config import get_settings
from app.domain.schemas import Evidence
from app.services import kb_ingest


def _ev(title: str, *, snippet: str = "", ident: str | None = None) -> Evidence:
    return Evidence(
        source="arxiv",
        identifier=ident or ("http://arxiv.org/abs/" + title.split()[0].strip("$_,;:")),
        title=title,
        snippet=snippet or title,
        relevance=0.9,
    )


# ── topic_gate 单元判定 ─────────────────────────────────────────────────────


class TestTopicGate:
    def test_relevant_coating_title_passes(self):
        # 磷化液 pH 对 AZ91D 磷酸盐转化膜 —— 高判别词(磷化/磷酸盐/转化膜)
        assert kb_ingest.topic_gate(
            "14 磷化液pH 值对磷酸盐转化膜在镁合金AZ91D 表面成型与耐蚀性的影响"
        )

    def test_heusler_magnetism_blocked(self):
        # 高判别 0 + 反向 magnetic/hall/heusler → 拦
        t = "Scaling Analysis of Anomalous Hall Resistivity in the Co2TiAl Heusler Alloy"
        assert kb_ingest.topic_gate(t) is False

    def test_hydride_storage_blocked(self):
        # Magnesium 低判 1 + block hydride → 拦
        assert kb_ingest.topic_gate("Tunable Hydrogen Storage in Magnesium - Transition Metal Compounds") is False
        assert kb_ingest.topic_gate("Dopant-vacancy binding effects in Li-doped magnesium hydride") is False

    def test_biomedical_corrosion_blocked(self):
        # corrosion 高判 1 <2 + block biomedical → 拦
        t = "Corrosion studies on Fe-30Mn-1C alloy in chloride solutions with view to biomedical application"
        assert kb_ingest.topic_gate(t) is False

    def test_hea_corrosion_blocked(self):
        # corrosion 高判 1 <2 + block high-entropy → 拦
        t = "Machine learning accelerated discovery of corrosion-resistant high-entropy alloys"
        assert kb_ingest.topic_gate(t) is False

    def test_diamond_cutting_tool_blocked(self):
        assert kb_ingest.topic_gate("Uniform diamond coatings on WC-Co hard alloy cutting inserts") is False

    def test_battery_blocked(self):
        assert kb_ingest.topic_gate("High-performance magnesium/sodium hybrid ion battery based on sodium vanadate") is False

    def test_astronomy_blocked(self):
        assert kb_ingest.topic_gate("Laboratory and astronomical discovery of magnesium dicarbide") is False

    def test_pure_mechanics_blocked(self):
        # 镁合金硬化/位错 —— 力学, 无腐蚀/涂层语境
        assert kb_ingest.topic_gate("Anomalous Hardening in Magnesium Driven by a Size-Dependent Transition") is False

    def test_two_base_metals_pass(self):
        # 低判别 ≥2: 镁+合金 → 放行(领域材料文献, 无反向词)
        assert kb_ingest.topic_gate("Advances in magnesium alloy AZ91 surface treatments") is True

    def test_single_base_metal_alone_blocked(self):
        # 仅 1 个低判别词且无高判别词 → 拦(信息不足)
        assert kb_ingest.topic_gate("Magnetism in magnesium under pressure") is False

    def test_patent_kind_exempt(self):
        assert kb_ingest.topic_gate("A method of manufacturing a turbine blade", kind="patent") is True

    def test_snippet_can_carry_the_signal(self):
        # 标题只有 Mg 缩写, 但 snippet 带腐蚀抑制上下文 → 放行(DFT 缓蚀研究)
        assert kb_ingest.topic_gate(
            "A density functional theory study of amino acids on Mg and Mg-based alloys "
            "corrosion inhibition of magnesium alloys",
        ) is True

    def test_hall_word_boundary(self):
        # "shall" 不该误伤为 hall
        assert kb_ingest.topic_gate("This coating shall comply with the salt spray standard") is True


# ── select_ingest_targets 集成 ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _fresh_settings(monkeypatch):
    monkeypatch.setenv("FORMUMIND_API_AUTH_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _select(evidence, project_id="p1", monkeypatch=None):
    # 固定项目锚(标题「含聚合物/树脂的乳液型镁合金钝化剂」会给出的锚)
    if monkeypatch:
        monkeypatch.setattr(
            kb_ingest, "_topic_anchor",
            lambda pid, q=None: ({"钝化", "树脂", "乳液"}, {"镁"}),
        )
    return kb_ingest.select_ingest_targets(evidence, project_id=project_id)


def test_select_drops_offdomain_keeps_relevant(monkeypatch):
    evs = [
        _ev("磷化液pH 值对磷酸盐转化膜在镁合金AZ91D 表面成型的影响"),
        _ev("Scaling Analysis of Anomalous Hall Resistivity in the Heusler Alloy"),
        _ev("Tunable Hydrogen Storage in Magnesium Compounds"),
        _ev("氨基硅烷偶联剂改性环氧树脂对镁合金防腐蚀涂层的影响", ident="http://x/a"),
    ]
    out = _select(evs, monkeypatch=monkeypatch)
    assert len(out) == 2
    got = [e.identifier for e, _ in out]
    assert "磷化液pH" in got[0] or "surface" in got[0]
    assert all("heusler" not in i.lower() and "hydrogen" not in i.lower() for i in got)


def test_select_without_project_id_passes_everything(monkeypatch):
    evs = [_ev("Heusler magnetism notes"), _ev("battery cathode review")]
    targets = kb_ingest.select_ingest_targets(evs)  # no project → anchor None → no filter
    assert len(targets) == 2


def test_select_uses_query_anchor_when_project_missing(monkeypatch):
    # search API 不带 project 上下文 → query 即锚(2026-09-04 实际场景回放)
    monkeypatch.setattr(kb_ingest, "_topic_anchor", lambda pid, q: ({"钝化", "树脂"}, {"镁"}))
    evs = [
        _ev("Tunable Hydrogen Storage in Magnesium Compounds"),
        _ev("磷化液pH 值对磷酸盐转化膜在镁合金AZ91D 表面的影响"),
    ]
    out = kb_ingest.select_ingest_targets(evs, query="镁合金 钝化剂 树脂 乳液")
    assert len(out) == 1
    assert "磷化液" in out[0][0].title


def test_select_when_project_has_no_anchor_passes_everything(monkeypatch):
    monkeypatch.setattr(kb_ingest, "_topic_anchor", lambda pid, q=None: None)
    evs = [_ev("Heusler magnetism notes")]
    assert len(kb_ingest.select_ingest_targets(evs, project_id="p9")) == 1


def test_select_respects_filter_switch(monkeypatch):
    monkeypatch.setattr(kb_ingest, "_topic_anchor", lambda pid, q=None: ({"钝化"}, {"镁"}))
    monkeypatch.setattr(
        "app.services.kb_ingest.get_settings",
        lambda: type("S", (), {"kb_ingest_topic_filter": False, "kb_ingest_max_docs": 0, "kb_ingest_min_relevance": 0.0})(),
    )
    evs = [_ev("Heusler magnetism notes")]
    assert len(kb_ingest.select_ingest_targets(evs, project_id="p1")) == 1


def test_patent_evidence_exempt_from_filter(monkeypatch):
    monkeypatch.setattr(kb_ingest, "_topic_anchor", lambda pid, q=None: ({"钝化"}, {"镁"}))
    evs = [
        Evidence(
            source="web",
            identifier="https://patents.google.com/patent/CN101671821B/zh",
            title="(12)发明专利",
            snippet="一种镁合金的表面处理方法",
            relevance=0.9,
        )
    ]
    out = kb_ingest.select_ingest_targets(evs, project_id="p1")
    assert len(out) == 1  # patent kind 豁免, 即使标题无领域词

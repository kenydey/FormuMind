# Hybrid 化学实体加成 A/B 报告（2026-10-02）

## 实施
- 开关：`kb_hybrid_entity_boost`（默认关），`config.py` + `env_flags.py` 已注册
- 语义：融合前对 BM25/cosine 归一化分量做加性 boost（与 legacy `_entity_boost`
  同量级 0.2/0.3），再进 weighted/RRF 融合；不直接加到 RRF 融合分上

## 第一轮：53 道 golden 题
- 结果：ΔnDCG@6 = +0.0000，0 题变化
- 根因：53 题中 0 题含可提取化学实体（CAS/分子式/牌号/SMILES），treatment 从未触发

## 实体回填（前置修复）
- 发现存量 2087 chunks 的 `meta.chem` 全空（`_attach_entities` 上线前索引）
- `scripts/backfill_chunk_entities.py` 回填：1164 行写入，1163 行带 chem

## 第二轮：实体题（3 题 × CAS 2530-83-8）
- 结果：ΔMRR = +0.0000，两臂 top-50 均无实体 chunk
- 根因：108 个含 CAS 的 chunk 全是 wiki chunk，被 `gate_chunk_indices`
  按设计排除出检索；非 wiki chunk 是合成测试文本，无真实实体
- 代码验证：treatment 确实执行（2087 次 `_entity_boost` 调用，108 chunk +0.3，
  融合前排名已变化），只是 gate 后无可见差异

## 结论：KEEP OFF（inconclusive，非负）
- 当前语料下实体加成无可测效果：检索语料中没有非 wiki 的实体富集 chunk
- 实现正确、开关保留；待真实实体语料入库后重测再决定是否默认开启
- 附带发现：legacy `search_chunks` 的 `_entity_boost` 在当前语料同样空转

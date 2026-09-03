# MolScribe / MolJSON / ChemFormula 进一步挖掘方案

- **状态**：待评审
- **日期**：2026-09-02
- **方法**：API 面 vs 已用面代码审计（与已兑现的 P0-PF 对比）

## 一、三依赖现状与可挖 API 面（实测）

### MolScribe（已兑现 ~85%，可挖 ~10%）

| 未用 API | 实测存在 | 落地场景 | 价值 |
|---|---|---|---|
| `return_atoms_bonds=True` → **原子坐标 (x,y) + 逐原子置信度** | ✅ | 识别质量**逐原子**审计：低置信原子标记→前端高亮可疑区域（比整图 confidence 细一个量级）；坐标→图结构覆盖验证 | ★★★★☆ |
| `molfile` 输出 | ✅（pred_dict 含） | RDKit 直接吃 molfile 免 SMILES 往返，保 3D/立体化学 | ★★★☆☆ |
| 批量 `predict_images` | ✅（支持 batch） | 文献多图批量识别（现逐张） | ★★★☆☆ |

### MolJSON（已兑现 ~55%，可挖 ~35%）⭐ 最大空间

| 未用能力 | 现状 | 落地场景 | 价值 |
|---|---|---|---|
| **atoms 只含 id+element**，缺分子式/官能团摘要 | ❌ | LLM prompt 注入时附 `{formula, mw, func_groups, aromatic_rings}`——LLM 数原子仍有压力时给**算好的**摘要 | ★★★★★ |
| **只注入全分子图** | P0 已做 | 「含某官能团？」问题只注入相关子结构（SMARTS 命中子图），省 token 且聚焦 | ★★★★☆ |
| **配方级多分子 MolJSON** | ❌ | 多成分（树脂+固化剂）联合注入→LLM 推理成分间反应（P-F 的 LLM 版） | ★★★★☆ |
| **前端可视化** | ❌（只存不展示） | moljson atoms/bonds → SVG 结构图渲染，用户看到识别的分子 | ★★★☆☆ |

### ChemFormula（已兑现 ~70%，可挖 ~25%）

| 未用 API | 实测存在 | 落地场景 | 价值 |
|---|---|---|---|
| **`hill_formula`** | ✅（Zn3(PO4)2→O8P2Zn3） | 材料 formula **规范排序**——去重/匹配时 Hill 序是唯一键（现在直接比字符串，等价式判不同） | ★★★★★ |
| **`sum_formula`/`name`** | ✅ | 材料 enrich 时 formula→可读名补全 | ★★★☆☆ |
| **`latex`/`unicode`** | ✅ | 前端展示化学式（报告/导出），替代手写 | ★★★☆☆ |

## 二、推荐方案（按价值×投入排序）

### M-A：MolJSON 富化——atoms 附分子式/物性摘要（0.5 天，5/5）
`smiles_to_moljson` 输出加 `meta: {formula, mw, func_groups[], aromatic_rings, hba, hbd}`（RDKit Descriptors 已装）——LLM prompt 注入时附在 MolJSON 旁，数碳/环/官能团直接引用算好的值，比 P0 的 +7pp 更进一步。

### M-B：ChemFormula hill_formula 规范化键（0.5 天，4.5/5）
材料/KG 公式匹配从字符串比较升级为 Hill 序比较——`Zn3P2O8` 与 `Zn3(PO4)2` 判同。替换 chem_extract 的公式去重、材料 enrich 的冲突检测。

### M-C：MolJSON 子结构聚焦注入（1 天，4/5）
识别问题里的官能团意图（SMARTS）→ 只注入命中子图的 MolJSON + 完整图缩略——省 token 且把 LLM 注意力引到关键区域。

### M-D：MolScribe 原子级置信度审计（1 天，4/5）
`return_atoms_bonds` 逐原子 confidence → 低置信原子对应的结构区域标记，前端提示「该分子区域识别可信度低，建议核对」——比整图 confidence 精准。

### M-E：MolJSON 前端 SVG 渲染（1-2 天，3.5/5）
atoms/bonds → 坐标布局（力导向或预计算）→ SVG。让「识别出什么」可视化，用户可核对。

### M-F：配方级多分子 MolJSON 注入（2 天，3.5/5）
树脂+固化剂双分子图 → LLM 推理反应匹配度（P-F 结构计量的 LLM 补充）——大改 prompt 需重测。

## 三、量化

| 项 | 投入 | 依赖兑现率提升 | 核心价值 |
|---|---|---|---|
| M-A MolJSON 富化 | 0.5 天 | MolJSON 55→70% | ★★★★★ 推理准确 |
| M-B Hill 规范化 | 0.5 天 | ChemFormula 70→85% | ★★★★★ 数据一致性 |
| M-C 子结构注入 | 1 天 | MolJSON 70→80% | ★★★★☆ token 效率 |
| M-D 原子置信度 | 1 天 | MolScribe 85→92% | ★★★★☆ 质检精度 |
| M-E SVG 渲染 | 1-2 天 | MolJSON 80→90% | ★★★☆☆ 可视化 |
| M-F 多分子注入 | 2 天 | MolJSON 90→95% | ★★★★☆ 反应推理 |

## 四、建议

**M-A + M-B**（1 天，纯本地、见效快）→ **M-C + M-D**（2 天）→ M-E/M-F 视需求。注意：M-C/M-F 改 LLM prompt 必须重跑 P0 benchmark 验证不退化。

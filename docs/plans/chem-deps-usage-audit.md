# FormuMind 化学依赖发挥度评估 + 升级方案

- **状态**：待评审
- **日期**：2026-09-01
- **方法**：代码级调用点审计（grep 全量调用 + 链路追踪），非印象评估

## 一、四依赖发挥度总览（实测）

| 依赖 | 代码调用量 | 业务链路接入 | 发挥度 | 一句话 |
|---|---|---|---|---|
| **RDKit** | 7 文件 / 13 处 MolFromSmiles / 4 处 Morgan | 校验+检索+指纹 | **85% 高** ✅ | 最扎实——SMILES 校验闭环（P1）、相似检索（结构图链路）、chem_extract 全依赖它 |
| **ChemCrow** | 5 文件 / 8 能力定义 | API 暴露 + 推荐 gap-fill | **50% 中** ⚠️ | name→SMILES/CAS 补全在推荐链路生效；但 **web_search 0 调用**、inverse_design 的 chem_screen 被注释关闭 |
| **MolScribe** | 5 文件 | ingestion + 结构图上传 | **60% 中** ⚠️ | PDF 图片识别已接（ingestion）；结构图上传是刚交付的新链路，但**只出 SMILES，识别结果未进推荐/推理** |
| **MolJSON** | 4 文件 | 校验 + 返回展示 | **15% 低** ❌ | **核心潜力（LLM 推理端结构零误差，P0 实测 +7pp）完全未发挥**——smiles_to_moljson 生成后只返回前端，从未进任何 LLM prompt |

## 二、核心价值匹配度分析

**FormuMind 核心价值**：AI 辅助金属表面处理配方研发（知识图谱 / 多方案推荐 / DOE / 多轮对话）

| 能力 | 现状 | 核心价值匹配 | 差距 |
|---|---|---|---|
| **结构校验闭环**（P1） | ✅ 已上线（识别端+推荐端） | ★★★★★ | 无——已是双保险 |
| **结构图→检索** | ✅ 刚上线 | ★★★★★ | 相似材料命中已通 |
| **LLM 结构推理**（MolJSON 进 prompt） | ❌ 未接 | ★★★★★ | **最大空白**——P0 证明 +7pp 但没接线 |
| **化学知识检索**（ChemCrow web_search） | ❌ 0 调用 | ★★★★☆ | 定义了能力但未接线 |
| **逆设计**（chem_screen） | ❌ 被关闭 | ★★★★☆ | network-bound 被注释 |
| **子结构检索**（SMARTS 已导入未用） | ⚠️ 2 处 MolFromSmarts | ★★★★☆ | 有工具未成功能 |

## 三、升级方案（按功能匹配度优先级排序）

### P0：MolJSON 进 LLM 推理上下文（匹配度 5/5，改动小收益大）

**现状**：`smiles_to_moljson` 生成后只返回前端；chat 的 structure.smiles/moljson 被丢弃。
**方案**：chat + recommend 链路把识别结构的 MolJSON 铺进 LLM prompt（"目标结构：{moljson}"），让 DeepSeek 推理时看到显式原子/键而非 SMILES 字符串——正是 P0 实测证明的 +7pp 场景。
**改动**：`app/services/llm.py` 或 prompt 组装处加 structure 上下文注入（1 文件 + 测试）。
**收益**：结构相关问答/推荐的结构推理零误差化，MolJSON 从「展示件」变「推理件」。

### P1：ChemCrow web_search 接线（匹配度 4.5/5，能力已装未用）

**现状**：`availability()` 报告 web_search 需 ChemCrow+SERPAPI，但 0 处调用。
**方案**：材料补全/推荐链路增加「化学网络检索」兜底——当 name_to_smiles/CAS 查不到时，用 ChemCrow web_search 查 PubChem/Sigma 等，把结果结构化回填。
**改动**：`chemtools.py` 暴露 web_search 包装 + recommend_pipeline 接线。
**收益**：长尾材料（非种子 32 种）的自动补全率提升，减少人工录入。

### P2：子结构检索（匹配度 4/5，SMARTS 已导入未成功能）

**现状**：`Chem.MolFromSmarts` 2 处但无独立检索功能。
**方案**：材料库增加「子结构过滤」——查询 SMARTS（如 `[NX3;H2]` 伯胺）→ 返回含该子结构的材料列表，供 DOE/替换筛选。
**改动**：structure_search.py 加 `substructure_hits()` + API 参数。
**收益**：从「相似结构」到「含某官能团」的精确筛选，DOE 因子选择更化学化。

### P3：逆设计 chem_screen 解封（匹配度 3.5/5，需权衡）

**现状**：`inverse_design.py` 里 chem_screen 被注释关闭（network-bound）。
**方案**：改为 RDKit 本地优先 + ChemCrow 异步补强，避免阻塞主链路。
**收益**：逆设计输出经化学合理性检查（价键/受控物质），但需处理网络延迟。

### P4：KG 结构实体深度（匹配度 3/5，中长期）

**现状**：KGEntity.smiles 已存但图谱检索未用结构相似。
**方案**：KG 查询加结构相似度维度（实体 smiles → Tanimoto → 相关实体）。
**收益**：知识图谱从文本关联升级到结构关联，但依赖 KG 数据质量。

## 四、量化收益预估

| 项 | 投入 | 收益 |
|---|---|---|
| P0 MolJSON 进 prompt | 0.5 天 | 结构问答推理准确率 +7pp（P0 实测基线） |
| P1 web_search 接线 | 1 天 | 长尾材料补全率 ↑，人工录入 ↓ |
| P2 子结构检索 | 1 天 | 化学筛选从模糊到精确 |
| P3 chem_screen | 1-2 天 | 逆设计安全性 ↑（有网络依赖风险） |
| P4 KG 结构维度 | 2-3 天 | 图谱检索化学化（依赖数据质量） |

## 五、建议

**立即做 P0**（半天，MolJSON 从展示件变推理件，兑现 P0 实测价值）；**P1/P2 适合下一个迭代**（各 1 天，填补定义未用能力）；P3/P4 视版本节奏安排。核心原则：**先兑现已装依赖的价值，再引新依赖**——目前四个依赖全部已装，价值兑现率 RDKit 85% > MolScribe 60% > ChemCrow 50% > MolJSON 15%。

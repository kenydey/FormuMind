# 化学结构图检索接入方案 — 图片创建项目 / 图片提问

- **状态**：待评审
- **日期**：2026-09-01
- **关联**：MolJSON P0/P1（已上线）、MolScribe 容器化（已上线）、materials 材料库（已存在）

## 一、可行性结论：✅ 完全可行，积木全在

| 所需能力 | 现状 | 位置 |
|---|---|---|
| 结构图 → SMILES | ✅ 已上线，端到端验证过（DGEBA 25原子4环保真） | `molscribe` 容器 + `validate_recognized_smiles` |
| SMILES 结构校验 | ✅ 已上线 | `moljson.validate_smiles`（RDKit 回读） |
| SMILES → MolJSON | ✅ 已上线 | `moljson.smiles_to_moljson` |
| 化学相似度（Tanimoto） | ✅ 已存在 | `chemtools.mol_similarity`（Morgan 2048bit） |
| 结构化 SMILES 锚点库 | ✅ 数据库已有 3 处 | `MaterialRow.smiles` / `KBProduct.smiles` / `KGEntity.smiles` |
| 检索注入 | ⚠️ 半成品 | `search_scoring` 提取了查询 SMILES 但 **boost 只用 CAS/formula，SMILES 提取后未用** |

**缺的只有三块**：前端图片上传入口、后端识别接线（图片→任务→SMILES）、检索注入（SMILES 命中材料库→材料名/CAS 进 query + 相似材料作为证据）。

## 二、必要性分析：值得做，但定位要清醒

**值得做的理由**（产品差异化）：
1. **配方研发的核心动作是「结构找结构」**——研发人员手里有目标结构（文献截图/竞品分析/自己画的），系统若能自动检索「含此结构或相似结构的材料/专利/文献」，是从文本检索到化学检索的质变。
2. 成本极低：积木全在，主要工作量是接线（估计 1-2 天）。
3. 已有基础设施支撑：材料库 30+ 种子材料带 SMILES，KG 实体也带。

**必须清醒的边界**（避免过度承诺）：
1. **聚合物/混合物无单一 SMILES**（自沉积涂料原料多为聚合物/分散体）——MolScribe 对这类图识别会失败或只出单体 SMILES，需优雅降级（提示用户或用单体近似检索）。
2. **知识库文本 chunk 里未必有 SMILES 文本**——结构相似检索的可靠锚点是材料库（结构化），对自由文本只能做「命中材料→材料名/CAS 二次检索」的间接路径。
3. MolScribe 识别慢（~53s/张，冷启动 35s）——体验上必须异步 + 缓存，不能阻塞提问。

## 三、架构设计

```
前端(创建项目表单/问答框)                        backend                           molscribe 容器
┌────────────────────────┐    POST /api/chem/structure  ┌──────────────────┐   send_task   ┌──────────────┐
│ [结构图上传] [提示词]   │ ───────────────────────────▶ │ 存图到共享卷      │ ────────────▶ │ MolScribe    │
│  └─ 前端预览 + 识别态    │ ◀─────────────────────────── │ /app/data/struct │ ◀──────────── │ 图→SMILES    │
└────────────────────────┘    {smiles, moljson, hits}    │ validate_smiles  │               └──────────────┘
                                                          │ 相似命中材料库     │
                                                          │ (Tanimoto≥0.6)   │
                                                          │ MolJSON 生成      │
                                                          └────────┬─────────┘
                                                                   ▼
                                              检索 query 注入：识别出的材料名/CAS
                                              + 相似材料名称 → hybrid_search / chat 上下文
```

**关键设计决策**：
1. **检索用 SMILES（指纹），推理用 MolJSON**——检索的黄金标准是 Morgan 指纹 Tanimoto（结构化、快），MolJSON 是给 LLM 推理看的铺平结构。两者分工，不混用。
2. **识别结果缓存**（图片 SHA-256 → 结果），同图不重复识别（MolScribe 太慢，必须缓存）。
3. **识别与检索分离**：`POST /api/chem/structure` 只做「图→SMILES+MolJSON+相似材料」，返回结果由前端注入后续请求；不隐式改项目/问答流程，保持各 API 纯净。
4. **相似命中用材料库**（MaterialRow.smiles + KBProduct.smiles），阈值 Tanimoto ≥ 0.6（与 chemtools 一致），top-5。

## 四、前后端实现清单

### 后端（backend）

| 文件 | 改动 |
|---|---|
| `app/api/chemistry.py` | 新增 `POST /api/chem/structure`（multipart 图片 + 可选 context）：存图到共享卷 → `validate_recognized_smiles` 走 molscribe 队列 → 校验 → `smiles_to_moljson` → 相似命中 → 返回 `{smiles, moljson, hits[], image_sha}` |
| `app/services/structure_search.py` | **新增**：`similarity_hits(smiles, top_k=5, threshold=0.6)` 扫 `MaterialRow`/`KBProduct` 的 smiles 字段，Tanimoto 排序；复用 `chemtools.mol_similarity` |
| `app/services/structure_cache.py` | **新增**：图 SHA-256 → 识别结果缓存（Redis，TTL 7 天） |
| `app/api/chat.py` / `projects.py` | 接受可选 `structure_image_sha` 或 `structure_hits` 参数——若有，把识别出的材料名/CAS/SMILES 拼进检索 query + 附相似材料作为上下文（不新增强制依赖） |

### 前端（frontend）

| 文件 | 改动 |
|---|---|
| `api.ts` | 新增 `uploadStructure(image)`（multipart POST /api/chem/structure） |
| `components/ResearchPanel.tsx` | 问答输入框旁加「📷 上传结构图」按钮：上传 → 识别 loading 态 → 返回后把结构摘要（SMILES + 相似材料名）附到提问上下文中（直观展示识别结果，可删可改） |
| 项目创建表单（`projectWorkspace` 相关） | 同样加图片上传，识别结果并入 requirement |

## 五、风险矩阵

| 风险 | 等级 | 缓解 |
|---|---|---|
| MolScribe 慢（53s/张）阻塞体验 | 高 | 异步 + 图片 SHA 缓存；前端 loading 态「识别中…」；可后续加「识别失败可跳过」 |
| 聚合物/混合物识别失败 | 中 | 失败时返回 `{recognized: false}`，前端提示「该图未能识别为单一结构，可继续用文字提问」；不阻断 |
| 相似命中噪声（Tanimoto 阈值过低） | 中 | 阈值 0.6 + 返回相似度分数 + 前端显示「相似度 xx%」供判断 |
| 图片安全（超大/非图文件） | 低 | 限制 ≤10MB，MIME 白名单（png/jpg/webp），存共享卷后立即删临时文件 |
| 材料库 smiles 覆盖不全 | 低 | 未命中时返回空 hits，走纯文本检索，不报错 |

## 六、实施步骤（评审通过后）

1. 后端：`structure_search.py` + `structure_cache.py` + `/api/chem/structure` 端点 + 单测（识别 mock + 相似命中真实材料库）
2. 前端：`api.ts` + ResearchPanel 图片按钮 + 项目创建表单图片按钮
3. 全量回归（1668 现有测试）+ 真实端到端（DGEBA 图上传→识别→相似命中）
4. 重建 backend/frontend 镜像部署（molscribe 已是最新，无需动）
5. 提交推送 main

## 七、交付物定义

- 创建项目：提示词 + 结构图 → 项目创建，requirement 自动含「目标结构 SMILES + 相似材料」
- 问答：文字 + 结构图 → 提问上下文含识别结构，检索能命中含该结构的材料/文献
- 两处共用 `POST /api/chem/structure`，识别结果均展示给用户（可编辑/删除）

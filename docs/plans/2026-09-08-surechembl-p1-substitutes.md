# SureChEMBL 专利化学相似 — P1（结构相似 + 专利证据）

状态：**已实现**（2026-09-08）  
前置：P0 名称/SMILES lookup（`b63b02b`）

## 目标

在材料替代漏斗中增加 SureChEMBL **结构相似候选 + 专利文档链接**，与 PubChem 并列（独立开关，不绑「含停产」）。

## 实现要点

| 组件 | 作用 |
|------|------|
| `surechembl_client.structure_search` | `POST /search/structure` → poll status/results（预算 ~5s） |
| `surechembl_client.documents_for_chemicals` | `POST /search/documents_for_structures` |
| `surechembl_alternatives.fetch_surechembl_alternatives` | 噪声过滤、阈值门槛、专利 attach |
| `find_substitutes` | `include_surechembl`（默认 true）→ `surechembl[]` / `surechembl_meta` / `layers_used` |
| `MaterialSubstitutionModal` | checkbox + 专利链接 + promote（`source=surechembl`） |

## 红线

- 不编造 CAS；不自动 bulk upsert
- 失败降级为空列表 + meta，不拖垮库内/PubChem
- 与 PubChem 按 SMILES/InChIKey 轻量去重
- molbloom 仍仅作 offline 成员徽章/预筛（非搜索 API）

## 验证

- pytest：`test_surechembl_alternatives.py` + substitution channel tests
- vitest：Modal SureChEMBL section
- 实网冒烟：IPDA → `layers_used` 含 `surechembl`，专利 Google Patents 链接

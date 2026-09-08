# 联网原料替代 — 接口草图与 Modal 线框

状态：已认可 · `include_external` **默认开** · 明确不做 `materials_only` / 静默全量入库  
关联：`POST /api/materials/substitutes` · 方案 A（PubChem 相似联网补召回）  
**后续**：四层漏斗（材料库 → 文献/KG → 结构 → AI）见 [`2026-09-08-substitutes-four-layer.md`](./2026-09-08-substitutes-four-layer.md)

---

## 1. 请求（扩展现有 `SubstituteRequest`）

```http
POST /api/materials/substitutes
Content-Type: application/json
```

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `requirement` | Requirement \| null | null | 与现网一致 |
| `formulation` | Formulation \| null | null | 与现网一致 |
| `domain` | string | `""` | 兼容字段 |
| `slot_index` | int \| null | null | 与 `material` 二选一 |
| `material` | string | `""` | 槽位材料名 |
| `limit` | int | `10` | **库内**候选上限（1–50） |
| `include_unavailable` | bool | `false` | 是否含停产（库内） |
| **`include_external`** | bool | **`true`** | 是否联网拉 PubChem 相似清单 |
| **`external_limit`** | int | `8` | 联网候选上限（1–25） |
| **`similarity_threshold`** | int | `85` | PubChem 2D 相似阈值（60–100） |

关闭联网：`include_external: false` → 响应与现网兼容（可无 `external` 或空数组）。

---

## 2. 响应（扩展现有报告）

```json
{
  "original": "SDS",
  "slot_index": 2,
  "role": "surfactant",
  "substitute_group": "anionic_sles_family",
  "base_metrics": { "cost_cny_per_kg": 12.3 },
  "identity": {
    "query": "SDS",
    "cas_no": "151-21-3",
    "smiles": "CCCCCCCCCCCCOS(=O)(=O)[O-]",
    "cid": 3423265,
    "source": "catalog|pubchem|none",
    "resolved": true
  },
  "candidates": [ /* 库内，字段不变；可多 source:"catalog" */ ],
  "total_considered": 6,
  "external": [
    {
      "name": "Sodium lauryl sulfate",
      "iupac_name": "...",
      "cas_no": "151-21-3",
      "smiles": "CCOS(=O)(=O)[O-]",
      "cid": 3423265,
      "formula": "C12H25NaO4S",
      "molar_mass": 288.38,
      "similarity": 0.92,
      "source": "pubchem_similar",
      "in_catalog": false,
      "catalog_name": null,
      "role_hint": "surfactant",
      "note": "结构相似；未做配方 Δ 预测（入库后可再算）"
    }
  ],
  "external_meta": {
    "enabled": true,
    "queried": true,
    "count": 5,
    "skipped_reason": null,
    "provider": "pubchem_fastsimilarity_2d"
  }
}
```

### 字段约定

- **`candidates`**：仅库内；继续带 `deltas` / `feasible` / `delta_confidence`。
- **`external`**：联网结构相似；**默认不算**全量配方 Δ（避免假「性能中性」）。
- **`in_catalog`**：若 CAS/SMILES/norm_name 已命中库，标 true 并填 `catalog_name`（UI 可提示「已在库，见上方」）。
- **`external_meta.skipped_reason`**：无 SMILES/CID、超时、开关关、依赖缺失等人类可读原因。
- **降级**：联网失败不 5xx；`external=[]` + `skipped_reason`。

### 选用入库（复用现有，不新开硬约束 API）

| 动作 | 调用 |
|------|------|
| 高置信（有 CAS 或 SMILES） | `POST /api/materials/propose` → 期望 `upsert` |
| 仅名称 | `propose` → `pending` 或跳过（质量门） |
| 已在库 | 前端直接当库内候选使用，不必再 propose |

**明确不做**：`materials_only`、联网结果静默全量入库、强制过滤库外。

---

## 3. Modal 线框

```text
┌─ 材料替代                                              ✕ ─┐
│ 选一个成分，查看库内替代（含预测偏离）与联网结构相似候选。   │
│                                                            │
│ [ 成分下拉 ▾ SDS · surfactant · 3% ]  [ 🔍 查找替代 ]      │
│                                                            │
│ ☐ 包含停产材料（默认关）                                    │
│ ☑ 联网检索替代（PubChem，默认开）  ← include_external      │
│                                                            │
│ 替换 SDS · 组 anionic… · 库内考察 6 · 联网 5               │
│ 鉴定：CAS 151-21-3 · SMILES CCO… · source=catalog          │
│                                                            │
│ ▸ 库内候选（优先）                                         │
│ ┌──────────┬──────┬─────┬─────┬──────┐                     │
│ │ 候选材料 │ 相似 │ 成本 │ VOC │ 可行性│                     │
│ │ SLES     │ 92%  │ -3% │ +1% │ 可用 │                     │
│ └──────────┴──────┴─────┴─────┴──────┘                     │
│                                                            │
│ ▸ 联网候选（结构相似 · 选用后可入库）                       │
│ ┌────────────────┬────────┬──────┬────────┬──────────┐     │
│ │ 名称 / IUPAC   │ CAS    │ 相似 │ 来源   │ 操作     │     │
│ │ Sodium lauryl… │ 151-…  │ 92%  │ PubChem│[入库并选用]│    │
│ │ （已在库：SDS）│ …      │ 99%  │ PubChem│ 见上方   │     │
│ └────────────────┴────────┴──────┴────────┴──────────┘     │
│ ⓘ 联网项未做配方 Δ；入库后可再点「查找替代」查看偏离。      │
│ 聚合物/仅商品名：若无法解析结构，显示 skipped_reason。      │
└────────────────────────────────────────────────────────────┘
```

### UI 行为

1. 默认勾选「联网检索替代」；取消则只打库内。
2. `skipped_reason` 非空时，联网区显示灰字说明（非错误横幅）。
3. 「入库并选用」成功 toast → 可选刷新库内列表；不自动改配方基因组（与现 Modal「只分析不写回」一致；写回若已有入口则复用）。
4. 宽度可略增至 ~54rem 以容纳两段表。

---

## 4. 后端模块草图

```text
substitution.find_substitutes(..., include_external=True, external_limit=8, ...)
  ├─ 现有库内池 + Δ
  ├─ resolve_identity(original_spec | lookup_chemical)
  └─ if include_external:
       external_alternatives.pubchem_similar(smiles|cid, threshold, limit)
         · httpx + 24h 缓存 · 超时 degrade → []
```

新文件建议：`backend/app/services/external_alternatives.py`  
开关（可选）：`FORMUMIND_EXTERNAL_SUBSTITUTES=false` 强制关联网（部署级）。

---

## 5. 验收（本切片）

- [x] 默认请求不带 `include_external` 时仍拉联网（default true）。
- [x] `include_external=false` 时 `external=[]` 且库内行为与旧版一致。
- [x] 无 SMILES/CID：不抛错，`skipped_reason` 有值。
- [x] 断网/超时：库内结果仍返回（mock / degrade）。
- [x] Modal 两段清单 + 默认勾选联网；「入库并选用」走 propose。

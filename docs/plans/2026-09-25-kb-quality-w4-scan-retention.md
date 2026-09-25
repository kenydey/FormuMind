# KB 质量 W4（路线图 W4+）：scan 度量 + 旗标化 retention / 停写 suppliers_json

> 状态：**已实现**（2026-09-25）  
> 前置：W3 soft-archive；评审要求「有度量再建」  
> 约束：不默认物理删；不默认停 dual-write；不扩 Wiki/Neo4j；不改 Claims/DOE

## 0. 本批做 / 不做

| 做 | 不做 |
|----|------|
| `GET /api/kb/stats` 增加 active/archived 源与切块、`scan_limit` / `scan_pressure` | 默认自动 TTL 删库 |
| `archived_at` + `POST /api/kb/retention/purge`（默认 dry_run；需 `confirm`+`days≥1`） | 热/冷分层存储、对象存储外移 |
| 旗标 `materials_suppliers_json_dual_write`（默认 True）；False 时写入归一化表后清空 JSON | 删除 `suppliers_json` 列 / 破坏读回退 |

## 1. 行为

### Scan 度量

| 字段 | 含义 |
|------|------|
| `sources_active` / `sources_archived` | 软归档拆分 |
| `chunks_active` / `chunks_archived` | join SourceDocument |
| `scan_limit` | `kb_search_scan_limit` |
| `scan_pressure` | `chunks_active / scan_limit`（封顶 1） |
| `scan_near_cap` | pressure ≥ 0.9 → 提示仍痛，可考虑 purge |

### Retention

- `kb_archive_retention_days`（默认 0）仅作推荐阈值展示；**不自动跑**
- `POST /api/kb/retention/purge`：`{days, confirm, dry_run}`；只删 `archived` 且超龄源（走既有 `delete_kb_source`）

### suppliers_json

- 默认仍 dual-write
- `materials_suppliers_json_dual_write=False` → `clear_json_projection`，读路径仍 hydrate from link 表

## 2. 验证

```bash
cd backend && python -m pytest -q \
  tests/test_kb_w4_scan_retention.py \
  tests/test_supplier_normalize.py -k dual_write
```

# Next：A′ 手工供应字段 + explain/闭环/scan/向导抛光

> 状态：**已实现**  
> 基线：`main@8e958d5`  
> 分支：`cursor/next-a-prime-supply-manual-and-polish`  
> 约束：供应字段**仅手工录入**（`price_source=manual`）；不爬价；不默认关 dual-write；不扩 Neo4j

## 落地

| # | 交付 |
|---|------|
| 1 | alembic 0031 + soft ALTER；`supplier_normalize` 手工价/交期/MOQ；MaterialsPanel 编辑；`stale_price` |
| 2 | `test_explain_score_smoke`：`_score_and_validate` 挂 explain |
| 3 | `FM_INLINE=1` golden DOE script |
| 4 | HubQualityPane `scan_near_cap` CTA |
| 5 | PathWizard 无项目禁用知识路径 |

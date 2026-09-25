# Next Top-5 落地（2026-09-25）

> 状态：**已实现**  
> 前置：KB W1–W5 已合入 `main @ b874d6c`  
> 分支：`cursor/next-top5-w1-w5-rollout`  
> 约束：不扩 Wiki/Neo4j；不默认 auto TTL 删库；不默认停 `suppliers_json` dual-write；Claims/DOE 边界不破

## 落地顺序

| # | 项 | 本批动作 |
|---|----|----------|
| 1 | Topicality shadow→真闸 | `kb_relevance_shadow` **默认 False**；文案指向 stats 回滚 |
| 2 | 检索期负向收缩 | `search_deny` 域词表 + 扩展 `negative_terms` + 打分扣/滤（`kb_search_deny_enabled`） |
| 3 | 灰度卷宗/Report 开闸 | `wiki_project_dossier_enabled` / `wiki_dossier_report_enabled` / `wiki_chat_save_draft` **默认 True**（auto_patch / LLM 叙述仍关） |
| 4 | auto_loop / auto_adopt 分阶段 | 全局默认仍关；开启时 `confirm` + 「将写入台账」提示 |
| 5 | Retention 运维 UX | 依赖管理：dry-run 预览 → confirm purge（`scan_near_cap` 高亮） |

## 不做

- 全局默认开 `auto_loop_on_sync` / `auto_adopt_next_doe_on_loop`
- 自动 TTL 物理删；关 dual-write
- Owner Phase 2；STORM 默认开；llm_wiki S6+

## 验证

```bash
cd backend && python -m pytest -q \
  tests/test_top5_rollout_defaults.py \
  tests/test_topicality_enforce.py \
  tests/test_domain_search_profiles.py \
  tests/test_auto_adopt_flag.py \
  tests/test_kb_w4_scan_retention.py \
  tests/test_grayscale_kg_maintrack_gate.py
cd frontend && npx vitest run src/components/LoopModal.w5.test.tsx
```

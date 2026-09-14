# Wiki Compiled Memory — 运维收尾（Ops）

状态：**已落地（2026-09-13）**  
承接：S1 / P2 / P3 已完成；本切片补 Hub 操作入口与 Q4 审阅契约预留。

## 交付

| 项 | 说明 |
|----|------|
| Hub 操作 | 重建 FTS、重建 Embed、编译当前 system 主题、标记已审 |
| API | `POST /api/wiki/fts/rebuild`、`/embed/rebuild`、`/pages/review` |
| Q4 预留 | front-matter `reviewed` / `human_override` + 同步 `unreviewed` flag；**非** WYSIWYG 编辑器 |
| 设置 | `wiki_embed_enabled` / `wiki_llm_themes_enabled` / `wiki_fts_enabled` 已在 EnvFlags |

## 文件

- `backend/app/services/wiki/review.py`
- `backend/app/api/wiki.py`（`/pages/review`）
- `frontend/src/api.ts`（rebuild / review clients）
- `frontend/src/components/knowledge-hub/HubWikiPane.tsx`
- `backend/tests/test_wiki_ops_review.py`

## 测试

```bash
cd backend && python3 -m pytest -q tests/test_wiki_ops_review.py
```

## 非目标

完整人编 UI、MkDocs 导出、Claims/DOE 解锁（仍见蓝图 §5）。

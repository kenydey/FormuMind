# S4 草稿运营化 — Chat/Research → queries/ → 卷宗 S8

状态：**已落地（2026-09-21）**  
关联：[`2026-09-20-llm-wiki-borrow-ranked-plans.md`](./2026-09-20-llm-wiki-borrow-ranked-plans.md) S4 · [`2026-09-14-wiki-hub-dossier-handtest.md`](./2026-09-14-wiki-hub-dossier-handtest.md)

---

## 0. 范围

| 做 | 不做 |
|----|------|
| Chat / Deep Research 回答 → **存为 Wiki 草稿**（`queries/project-{id}-*.md`） | 自动写 Claims / DOE bounds |
| Hub Research 面板「存为 Wiki 草稿」按钮（旗标关默认隐藏） | llm_wiki 整页 LLM 编译 |
| 卷宗 pack `query_drafts` + **S8 待审列表** | S6/S7 借刀（已停表） |

旗标：`FORMUMIND_WIKI_CHAT_SAVE_DRAFT`（默认 **false**；灰度/手测再开）

---

## 1. 自动化

```bash
# 单测：草稿路径、DOE 隔离、S8 卷宗 feed
cd backend && python -m pytest -q \
  tests/test_wiki_chat_save_draft_s4.py \
  tests/test_wiki_p4_dossier.py -k query_drafts

# 金样 R&D 闭环（需活栈 + 五表有料 + S4 草稿 + Report）
python3 scripts/golden_rd_loop_smoke.py
FM_ENABLE_DRAFT=0 python3 scripts/golden_rd_loop_smoke.py   # 跳过 S4 步
```

---

## 2. Hub 手测（S4 运营）

| # | 步骤 | 期望 |
|---|------|------|
| D1 | Settings / env-flags 开 `wiki_chat_save_draft` | API `POST /api/wiki/drafts/save` 200 |
| D2 | 项目 Chat 或 Research 得回答 →「**存为 Wiki 草稿**」 | toast 成功；path 以 `queries/project-` 开头 |
| D3 | Hub Wiki 列表（project 过滤） | 草稿出现在 `queries/`；flags 含 `unreviewed` + `draft` |
| D4 | 卷宗 → **刷新** | S8 出现「L2 草稿（queries/）」列表；**不**改 S1–S6 数值表 |
| D5 | Claims / DOE hint | 草稿 **不进** Claims evidence；DOE bounds **不吃** query 页 |

---

## 3. 与金样闭环关系

`scripts/golden_rd_loop_smoke.py` 在真实项目上：

1. ingest 文献 + workspace（DOE/leaderboard/rmse）
2. 提交实验台账
3. ensure / refresh 卷宗 → 断言 S1–S6 非空
4. （可选）S4 草稿 save → S8 含 `queries/`
5. briefing + export MD（`draft_not_claims`）

卷宗/Report 五表有料后，S4 草稿仅作 **L2 待审运营**，不替代 L1 确定性切片。

---

## 4. 验收签字

- [x] `POST /api/wiki/drafts/save` + Hub 按钮
- [x] pack `query_drafts` + `pending_query_drafts` flag
- [x] S8 列出待审草稿（不进 Claims/DOE）
- [x] `golden_rd_loop_smoke.py` 活栈绿
- [x] pytest S4 + dossier S8

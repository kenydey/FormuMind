# 互联网兜底检索升级方案 — Google Custom Search JSON API（替代 DuckDuckGo / SearXNG）

- **日期**：2026-08-30（v2，v1 的 SearXNG 方案经评审后弃用，原因见 §0.2）
- **背景**：用户评估 `FORMUMIND_WEB_SEARCH_ALLOW_DDGS` 后，希望寻找免费（无 API key 成本）且检索质量**明显高于 DuckDuckGo** 的兜底档。
- **状态**：**已否决不实施**（2026-08-30 用户决策：零改动。Tavily+SerpAPI 双高质量主链路已配，DDG 保留为最后保险；Google CSE 2025 起关闭新注册、2027-01-01 关停；Python 免 key 聚合库已被反爬打废）
- **关联**：`app/services/search_providers.py`（provider 模式）、`app/services/literature.py:438-474`（search_internet 兜底链）、`app/config.py:246`（web_search_allow_ddgs）、`app/services/env_flags.py:63`（EnvFlag）、`app/services/secrets_store.py:34-35`（secret 注册）、`app/services/runtime_secrets.py:74`（effective_setting）

---

## 0. 调研结论（2026-08-30 实证）

### 0.1 候选评估
| 候选 | 结论 | 依据 |
|------|------|------|
| **Google Custom Search JSON API** | ✅ **选它** | 官方通道免费 **100 次/天**（超出 $5/1000 次，硬顶 10,000/天）；**零反爬**（官方 API 非 HTML 抓取）；Google 原生索引质量；无需容器 |
| SearXNG 自托管 | ❌ 弃用 | 2026-01 社区实证（github #5651/#5286）：HTML 抓取模式下 **Google 长期 down、Bing 质量差、Brave 限流、DDG 验证码**——「多引擎冗余」纸面成立、现实脆弱；且需 +150MB 容器运维 |
| Brave Search API | ❌ | 2025 底砍纯免费档：新用户仅 $5/月 credits（~1000 次），需绑卡 |
| Mojeek API | ❌ | 免费档不明确/额度低，索引规模远小于 Google |
| OpenSERP / Startpage / Firecrawl | ❌ | 爬虫抓取稳定性差 / 无公开 API / 定位不符 |

### 0.2 为什么弃 v1 SearXNG 方案
- v1 缓解「Google 反爬」靠多引擎冗余 + 落回 DDG。但社区 2026-01 实证：HTML 抓取对 **Google/Bing/Brave/DDG 四大引擎全部失效或降级**——冗余缓解不成立。
- **换通道而非换引擎**：Google CSE 走官方 API，反爬问题结构性消失；质量 = Google 原生（现 SerpAPI 同源）；免费 100 次/天对 FormuMind 检索量（用户触发的研究/推荐任务）绰绰有余。
- 顺带消除 v1 的容器运维/内存/端口风险——**纯 provider 函数，零基础设施**。

### 0.3 代码事实核查
| 机制 | 现状（代码证据） | 本方案复用 |
|------|----------------|-----------|
| Provider 函数 | `search_providers.py:243 search_tavily` / `:288 search_serpapi_web`：`(query, limit, offset, *, settings)` → `list[Evidence]`，httpx + `degrade_return` | `search_google_cse` 同签名同模式 |
| Secret 注册 | `secrets_store.py:34-35`：`("serpapi_api_key", "FORMUMIND_SERPAPI_API_KEY", "SerpAPI", "search")` 元组注册 → 设置面板可配 + RuntimeSecrets 叠加 | 注册 `google_cse_api_key` 同模式；`google_cse_cx` 非密钥走普通 settings |
| 兜底链入口 | `literature.py:438 search_internet`：Tavily → SerpAPI → (DDG 若 `web_search_allow_ddgs`) | 在 DDG 档前插入 Google CSE 档 |
| 开关语义 | `literature.py:468`：`if not getattr(settings, "web_search_allow_ddgs", True): return []` | 新增 `web_search_allow_google_cse`（默认 True，但**无 key 时跳过** = 等价关闭） |
| 设置面板 | `env_flags.py:63` EnvFlag 注册 → `api/settings.py:289` 自动暴露 | 新增 1 条 EnvFlag |

---

## 1. 升级目标

1. **零成本质量跃升**：兜底档从 DDG（弱）升级为 Google CSE（Google 原生索引，免费 100 次/天），**无容器、无反爬、无付费**
2. **严格向后兼容**：Tavily/SerpAPI 优先级不变；未配置 Google CSE key 时行为与现状完全一致
3. **可归因可开关**：结果 `source="Google CSE"` 可见；`FORMUMIND_WEB_SEARCH_ALLOW_GOOGLE_CSE=false` 可关；设置面板可见
4. **配额透明**：100 次/天配额耗尽时（HTTP 429）优雅落回 DDG 档或返空，绝不报错中断

---

## 2. 架构图

```
当前兜底链（literature.py:438 search_internet）：
  Tavily (key) → SerpAPI (key) → [DDG 若 web_search_allow_ddgs]

升级后：
  Tavily (key) → SerpAPI (key) → Google CSE (免费 100/天) → [DDG 若 web_search_allow_ddgs]
                                    │
                                    ▼
              GET https://www.googleapis.com/customsearch/v1
              ?key=FORMUMIND_GOOGLE_CSE_API_KEY&cx=FORMUMIND_GOOGLE_CSE_CX
              &q=...&num=min(10, limit+offset)
                                    │
                                    ▼
              search_providers.search_google_cse() → list[Evidence] (source="Google CSE")

配置面：
  .env:  FORMUMIND_GOOGLE_CSE_API_KEY=...   (Google Cloud Console 免费申请)
         FORMUMIND_GOOGLE_CSE_CX=...        (Programmable Search Engine ID，免费)
         FORMUMIND_WEB_SEARCH_ALLOW_GOOGLE_CSE=true
  secrets 面板: secrets_store.py 注册 → 设置面板可改 key
  env_flags 面板: 「Google CSE 兜底检索」开关
```

---

## 3. 文件变更清单

| # | 文件 | 改动 |
|---|------|------|
| C1 | `backend/app/services/search_providers.py` | 新增 `search_google_cse(query, limit, offset, *, settings)`：读 `google_cse_api_key`（effective_setting）+ `google_cse_cx`；无 key 或无 cx 或 `web_search_allow_google_cse=False` → `[]`；GET `https://www.googleapis.com/customsearch/v1`（params: key/cx/q/num/start）；归一化 `items[]` → Evidence（`source="Google CSE"`、`identifier=link`、`title`、`snippet[:500]`、relevance 递减）；httpx 25s 超时 + `degrade_return` 吞错；429 → 返回 `[]`（让上层落回 DDG） |
| C2 | `backend/app/services/literature.py` | `search_internet`（:438）：SerpAPI 档后、DDG 档前插入：`if getattr(settings, "web_search_allow_google_cse", True): hits = search_google_cse(...); if hits: return hits`；DDG 逻辑不变（仍是最后一级） |
| C3 | `backend/app/config.py` | Settings 新增 `google_cse_api_key: str \| None = None`、`google_cse_cx: str \| None = None`、`web_search_allow_google_cse: bool = True`（注释：免费 100 次/天；无 key 自动跳过） |
| C4 | `backend/app/services/secrets_store.py` | 注册 `("google_cse_api_key", "FORMUMIND_GOOGLE_CSE_API_KEY", "Google CSE", "search")`；`google_cse_cx` 不入 secrets（非密钥，走 .env） |
| C5 | `backend/app/services/env_flags.py` | 新增 EnvFlag：`("web_search_allow_google_cse", "Google CSE 兜底检索", "Tavily/SerpAPI 无结果后，用 Google Custom Search（免费 100 次/天，官方 API 零反爬）兜底。未配置 key/cx 时自动跳过。", "retrieval", "需 Google Cloud Console 免费申请 API key + Programmable Search Engine ID")` |
| C6 | `backend/tests/test_search_providers.py`（新增） | ① 无 key/cx → `[]` 不报错 ② 开关 False → `[]` ③ httpx mock 返回 items → Evidence 归一化正确（source/identifier/title/snippet）④ 网络异常 → `[]` ⑤ HTTP 429 → `[]`（不抛异常）⑥ `search_internet` 链：SerpAPI 空 + CSE 有 → 走 CSE；CSE 空 → 落 DDG（若开启）或返空 |
| C7 | `.env` | 新增注释模板：`FORMUMIND_GOOGLE_CSE_API_KEY=` / `FORMUMIND_GOOGLE_CSE_CX=` / `FORMUMIND_WEB_SEARCH_ALLOW_GOOGLE_CSE=true`（留空 = 不启用，行为与现状一致） |

---

## 4. 实施步骤（时间表）

| 阶段 | 任务 | 关键文件 | 验证 |
|------|------|---------|------|
| S1 | config + secrets 注册 + env_flags | config.py / secrets_store.py / env_flags.py | `import app.main` 成功；设置面板可见 secret + 开关 |
| S2 | `search_google_cse` provider 函数 | search_providers.py | 无 key 返回 `[]`；mock 测试归一化正确 |
| S3 | literature.py 兜底链插入 | literature.py | 单测：链顺序（SerpAPI→CSE→DDG）；429 优雅落回 |
| S4 | 测试（C6 全部用例）+ 全量回归 | tests/ | 新增全绿；**全量 pytest 无回归** |
| S5 | 真实端到端：**用户提供免费 key/cx** → 真实查询 → Evidence.source="Google CSE" 且质量可用 | API | curl 实测 2-3 个自沉积查询，来源标注正确 |
| S6 | 提交（feat 单 commit）+ 推送 main | — | SSH push 成功 |

改动估算：6 文件（1 新增测试），~200 行。纯增量。**零容器、零基础设施**（相较 v1 SearXNG 的 compose 服务 + settings.yml + healthcheck 大幅简化）。

---

## 5. 风险矩阵

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| **配额耗尽（100 次/天）** | 中（重度使用日） | 低 | 429 → `degrade_return` 返回 `[]` → 自动落回 DDG 档（保留最后一级），链路不中断；设置面板可见 key，可临时换付费或关闭 |
| **Google CSE 是「网站搜索」配置错误**（cx 只搜指定站点而非全网） | 低 | 高 | 文档强调：Programmable Search Engine 创建时选 **「Search the entire web」**；DoD 端到端用真实查询验证结果广度 |
| key 泄露（写入 git） | 低 | 高 | secrets_store 注册走环境变量 + RuntimeSecrets 叠加（与 SerpAPI/Tavily 同机制，不落库明文）；.env 不入库（现有约定） |
| Google API 自身故障/限流 | 极低 | 低 | 官方通道稳定性远高于 HTML 抓取；失败吞错落回 DDG |
| 结果被误判为文献 | 低 | 中 | `source="Google CSE"` 走 `_is_weblike` 判定（与 SerpAPI (web) 同路径），**不加**文献类后缀（延续 search_serpapi_web :311-317 命名教训） |
| 用户没有 Google 账号/不愿申请 | 中 | 无 | 纯可选增强——不配置即行为不变（DDG 兜底保留）；开关可关 |

---

## 6. 回滚方案

- **纯增量**：不删改既有 provider/开关/链顺序，仅插入新档
- 回滚三层：① `FORMUMIND_GOOGLE_CSE_API_KEY=` 留空 → 行为与现状完全一致（立即生效）② `web_search_allow_google_cse=false` ③ `git revert <commit>`
- 无容器/无数据迁移——回滚成本趋近于零

---

## 7. 验收标准（DoD）

- [ ] `search_google_cse` 无 key / 开关关 / 网络异常 / 429 四种情况均返回 `[]` 不抛异常
- [ ] `search_internet` 链顺序：Tavily → SerpAPI → Google CSE → DDG（`web_search_allow_ddgs=true` 时）
- [ ] CSE 命中时 `Evidence.source == "Google CSE"`，且不被 `_is_patent_or_literature` 误判为文献
- [ ] 设置面板可见「Google CSE 兜底检索」开关 + secret 配置项
- [ ] 新增测试全绿；**全量 pytest 无回归**；真实查询端到端确认来源标注
- [ ] 未配置 key 的部署（如测试环境）行为与升级前完全一致
- [ ] （需用户配合）免费申请 key/cx 后真实验证 Google 原生质量 ≥ 预期

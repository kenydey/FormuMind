# FormuMind 多源文献与专利自动化摄取 — 现状审查与增量升级方案

日期: 2026-09-05
性质: 对"多源文献与专利自动化摄取架构方案"的逐条实码审查(非重写)——先核对现状, 纠正方案中不现实/不可行项, 只对真实缺口做增量。

## A. 审查结论速览

**方案描述的架构在现有代码库中已实现 90%+, 且多处超越方案**(检索探针更多、PDF 解析级联更细、arXiv 源码直取已带实测数据)。真实缺口为: Celery Beat 雷达任务(无)、少量管道编排整合(可选)、方案中的重型/违规项需明确拒绝。

| 方案章节 | 方案设想 | 现状(证据) | 差距 |
|---|---|---|---|
| §2 统一多源搜索 | Tavily / 专利库 / 学术库探针 | **已有且更全**: `federated_search.py` + `search_providers.py` — OpenAlex(`search_openalex` L73)、SerpAPI scholar/patents/web(L147/179/288)、Tavily(L243)、**Google Patents CN(L331)+ CNIPA 并行(L352)**、`literature.iter_search` 封装 | 无(比方案多 CNIPA) |
| §2 ChemRxiv 探针 | 化学预印本 | 无 | 建议**不做**: ChemRxiv 以合成化学为主, 金属表面处理配方领域命中价值低 |
| §3 梯度1 源码直取 | arXiv e-print LaTeX | **已实现且实测**: `arxiv_source.py`(471 行)全文, docstring 实测 100 页论文 PDF 53s→LaTeX 1.2s、公式保真、标题结构保留 | 无 |
| §3 梯度1 PMC JATS / EPO ST.36 XML | 官方 XML | `fulltext_fetcher._fetch_literature_text`(L163): arXiv 源码优先→Unpaywall OA PDF; `_resolve_oa_pdf_url`(L124) OA 探测; 专利走 Google Patents DOM(免 XML 解析成本) | 无(路线不同但等效) |
| §3 梯度2 语义 DOM | Google Patents 语义 HTML | **已实现**: `pdf_downloader.py` docstring — patents.google.com `<section itemprop=abstract/description/claims>` 直取 + CN/JP/KR 自动英译 + `citation_pdf_url` | 无 |
| §3 梯度3 PDF/OCR | MinerU | **已有更务实替代链**: `parsing.py` 级联 pypdf→markitdown→rapidocr→docling→marker→(有 token 时)云 MinerU; `rapidocr_local.py`(PP-OCRv4 CPU, 注释实测 docling/marker ~54s/页、**本地 MinerU 需 GPU — 本机无**); `vision_extract.py` — 视觉 LLM 直出表格 Markdown/结构式 SMILES + RDKit 校验 | 无(MinerU 在有 token 部署可用; 无 token 由 rapidocr/vision 替代) |
| §3 化学特征+KG | CAS/分子式/SMILES 抽取、配方表结构化 | `chem_extract.py`(351 行): chunk 级离线 CAS 校验位/分子式解析/SMILES RDKit 规范化/反应方程 + anti-acronym; `vision_extract` LLM 级 + RDKit 复验 | 配方**实施例配比表→结构化行(成分+Role)**: 实体级有(`kg/entity_linker`/`formulation_linker`), 表→DataFrame 角色行的完整通道待核(P3) |
| §4 Celery 状态机 | 单任务三梯度降级, 成功即终止 | `fulltext_fetcher.py`(426 行): 按 evidence kind 注册表路由(patent/literature/web), **kind 内已级联**: 文献=arXiv 源码(成功即返回)→OA PDF; 注释明确"source first because PDF is where the time goes" | 无(现有编排=该状态机的实现) |
| §4 后台任务 | kb_ingest 回填 | `kb_ingest.py` + `worker/tasks.py:652 formumind.kb_ingest`: search/research 后自动派发, `select_ingest_targets` + **topic_gate 主题门控**(过滤无关源)+ content_hash 幂等 | 无 |
| §5 幂等/重试 | Redis 已处理标识符 | `source_documents.content_hash` 幂等 + `api/_idempotency.py`(Redis) | 无 |
| §1 Celery Beat 雷达 | 周期性自动检索摄取 | **无 crontab/periodic_task** | **真缺口(唯一较大项), P2** |
| §5 反爬对抗 | DrissionPage/Playwright 集群 + SOCKS5 代理池 | 无(仅规范 UA + `_is_safe_url` SSRF 防护) | **建议不引入**(见 B2) |
| §5/§3 Sci-Hub / CNKI CAJ 破解 | 盗版兜底 | 无(正确) | **建议明确拒绝**(见 B1) |

## B. 方案中建议拒绝/纠正的项(逐条理由)

**B1. Sci-Hub 兜底 + 中文知网 CAJ 破解 → 不引入。**
版权侵权风险(商业期刊 + 国内数据库), 且与你既定的数据源质量原则冲突: "无法下载全文的资料源对 FormuMind 无价值, **强制过滤**(不做功能开关)"。Sci-Hub 下载的 PDF 版面质量参差、无稳定镜像, 维护成本高。已有 Unpaywall OA 探测(`_resolve_oa_pdf_url`)覆盖合法 OA; 其余按现状丢弃即过滤。

**B2. 代理池 + 无头浏览器集群 → 不引入。**
现实约束: 部署机 4 核 E5-2690 v2(无 AVX2)+ 6.3GB RAM 无 GPU(torch 受限 ≤2.3.0+cpu)。`rapidocr_local.py` 注释已实测记录同类结论(docling/marker 54s/页、MinerU 需 GPU)。代理池+Playwright 集群在本机不可行; 若未来某源确需浏览器级渲染, 应走后端"浏览器兜底"服务化(`headless-browser-cdp-fallback` 类单实例), 而非代理集群。

**B3. MinerU 作为梯度3 主角 → 维持现状的级联定位。**
现有 `parsing.py` 已含云 MinerU 路径(有 token 时启用), 无 token 由 RapidOCR+视觉 LLM 覆盖。方案中 MinerU 的"双栏切块+表格还原"收益与 rapidocr+vision 重叠, 不值得提升优先级。

**B4. ChemRxiv 探针 → 不做(领域价值低), 如需扩展预印本应优先 arXiv + OpenAlex 覆盖度。**
代码库已能通过 OpenAlex(含 ChemRxiv 收录)发现化学预印本元数据, 缺的只是"下载源码"一步, 而 ChemRxiv 化学合成主题与 FormuMind 配方研发(金属表面处理)交集小。

**B5. Crawl4AI → 本期不引入为常驻依赖; 若未来出现 JS 渲染需求, 用"按需 Playwright 渲染 + 现有 trafilatura 解析"而非 Crawl4AI。**
核实(2026-09-05, 官方仓库/0.5.0 release): Crawl4AI = Playwright(Chromium)之上的异步爬虫封装, 开箱提供 JS 渲染、内容过滤(Pruning/BM25)、LLM 提取、深度爬取; 代价是 **Chromium 本体(Docker ~2GB; 库 + `playwright install chromium`)+ 常驻浏览器进程内存**, 且 0.5.x 正处于 API 大改期(维护风险)。
- 与现状对照: `html_to_markdown`(parsing.py L398)已用 trafilatura(保表格、去样板、Markdown 输出)+ regex 兜底——FormuMind 网页摄取的真实目标(供应商 TDS/静态配方页/OA 期刊/Google Patents DOM)绝大部分**静态或轻动态, trafilatura 已覆盖**(本日 9 源导入全部成功佐证);
- 本轮实测失败的两源(SCIRP/MDPI)是 **Cloudflare 类防护**, 而此类防护识别 headless Chromium, Crawl4AI 的 stealth 只缓解不保证——"天然绕过各种反爬"是营销话术, 需打折扣; 真正稳定对抗需要真实指纹 + 高质量代理 IP(即 B2 已拒绝的集群方案);
- 若确有 JS 渲染场景(供应商站成分表异步加载), 正确增量是 **懒加载浏览器兜底**: 后台任务内按需 `playwright` 起 Chromium → 渲染拿 HTML → 交现有 `html_to_markdown`——浏览器进程即用即弃(不常驻 uvicorn), ~60 行, 与 B2"单实例浏览器兜底"一致; Crawl4AI 的过滤/深度爬取能力与现有 chunk 级 topic_gate/Pruning 重叠, 收益不足以换取 2GB 依赖 + API 迭代风险。

## C. 真实缺口与推荐实施(增量, 由大到小)

### P1(建议本期做): 摄取管道编排收口 — 单入口 URL/DocumentTask → 全链
现状: `api/ingest/url`(单 URL)、`kb_ingest`(检索回填)各自独立调 fetcher/index, **没有面向"用户给一个 DOI/专利号/arXiv id 就完整走一遍三梯度"的统一入口**(现状是给 URL 或经检索证据)。方案 §4 的 `DocumentTask`(doc_type+identifier+extra_meta)仍是合理的收敛点。

- 新增 `app/services/document_task.py`: 接受 `{doc_type: patent|paper|web, identifier: DOI|arxiv:id|patent号|URL}`, 内部路由到现有能力:
  - paper: DOI → OpenAlex 找 OA → arXiv 源码优先 → Unpaywall PDF(全复用 `fulltext_fetcher`);
  - patent: 专利号 → `pdf_downloader` DOM 直取(复用);
  - web: URL → trafilatura(复用 `ingestion.ingest_url`)。
- API: `POST /api/ingest/task`(对齐现有 `ingest/url` 返回 IngestResponse)。
- 幂等: identifier 归一(DOI 小写/arXiv 去版本)查 content_hash 前先查 source_documents 的 URL 指纹。
- 代码量: ~150 行(编排)+ ~40 行(API)+ 测试。**纯复用, 不新引依赖。**

### P2(建议本期做, 量小): Celery Beat 雷达 — "主题定期复查"
现状无定时任务; 运维记忆里 celery 是手动起的(无 beat)。价值: 对镁合金钝化类主题定期(如每周)检索新专利/文献并 ingest(增量 content_hash 幂等天然去重)。
- `worker/celery_app.py` 加 `beat_schedule`(若启动加 `-B`); 但**手动起 celery 的运维现状无 beat 进程** → 落地方式: 提供 `dispatch_topic_sweep(topic_query, project_id)` 单次触发 API + 可选 beat 配置, 由你在 dev/生产按需起 beat, 不改变默认启动路径。
- 主题持久化: 复用 project `workspace.search_query`(已有)作为雷达主题源。
- 代码量: ~80 行 + 测试。

### P3(条件项, 不做常驻): 网页 JS 渲染兜底(浏览器级, 按需)
仅当实际遇到"静态抓取返回空/无正文, 而该源确属高价值"(如供应商成分表异步加载)时实施——**不是默认引入**。实现: `app/services/browser_fetch.py`(~60 行)后台子进程按需起 Chromium → 渲染 + 等网络空闲 → 取 HTML → 复用 `html_to_markdown`。挂载点: `parsing.py` 级联 HTML 分支的最末层(`html_to_markdown` 返回空文本后触发, 同 rapidocr 在文本层解析失败后才接力的模式)。不引 Crawl4AI(理由见 B5)。

### P4(评估项, 非本期): 实施例配比表 → 结构化行
`chem_extract` 已做 chunk 实体抽取; "配方表列→成分+用量+Role(成膜树脂/缓蚀剂/交联剂)结构化行"若确有下游需求(推荐引擎直接吃结构化配方), 需先核 `kg/entity_linker` + `formulation_linker` 的覆盖度再定(可能已部分覆盖)。**本期只做核验, 不动代码。**

## D. 文件变更清单(P1+P2)

| 文件 | 动作 | 内容 |
|---|---|---|
| `app/services/document_task.py` | 新增 | DocumentTask 编排(纯复用 fetcher/parsing) |
| `app/api/ingest.py` | 修改 | +`POST /ingest/task`(~40 行) |
| `app/worker/tasks.py` | 修改 | +`formumind.topic_sweep` 任务(~50 行) |
| `app/worker/celery_app.py` | 修改 | +beat_schedule(注释开关, 默认不启) |
| `tests/test_document_task.py` | 新增 | 编排/降级/幂等测试(mock fetcher, 不触网) |
| `tests/test_topic_sweep.py` | 新增 | 雷达单次触发测试 |

## E. 实施时间表

| 步骤 | 内容 | 估时 |
|---|---|---|
| 1 | P1 document_task 编排 + API + 测试 | 0.5 天 |
| 2 | P2 topic_sweep + beat 配置 + 测试 | 0.5 天 |
| 3 | 全量回归(后端全绿)+ 端到端(DOI/专利号/URL 三种 identifier 实导) | 0.5 天 |
| 4 | 提交推送 | — |

## F. 风险矩阵

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| document_task 对非 OA/高墙源大面积失败 | 高 | 低 | 失败返回明确原因(与现状"强制过滤"一致); 不降级盗版 |
| DOI→OA 解析慢(Unpaywall 网络) | 中 | 低 | 复用现有超时+异步任务路径, 前端可轮询 |
| beat 引入后 celery 重启行为变化 | 低 | 中 | 默认不启 beat(注释开关), 文档注明手动起须带 `-B` |
| 编排层引入循环依赖 | 低 | 中 | document_task 只 import services 层, 函数内延迟导入 |
| 配方表结构化(若 P3 做)范围蔓延 | 低 | 中 | 本期不实施, 先核 coverage |

## G. 结论

方案 = 一份"外部视角的架构蓝图", 而代码库经过多轮迭代已经实现并超越了它(证据见 A 表)。**不建议大改; 建议只做 P1+P2 两个增量收口**; P3 浏览器兜底仅在高价值 JS 源实证出现后才实施(且用裸 Playwright 而非 Crawl4AI, 见 B5); 明确拒绝 Sci-Hub/代理集群/MinerU 主角化/ChemRxiv 四项。若你认可, 我按 D/E 执行 P1+P2。

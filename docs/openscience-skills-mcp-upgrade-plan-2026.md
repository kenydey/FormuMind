# FormuMind 向 Evidence Synthesis 演进：Skills + MCP 统一管理升级方案（2026-09-27）

> 输入：`aipoch/open-science`（v0.33.3）与 `synthetic-sciences/OpenScience`（commit 132dfdf）只读代码分析、
> FormuMind 前端现状地图、PaperQA2 机制核实。两个仓库已克隆至 `~/workspace/research/`。

## 1. 一句话结论

**三个提议都值得做，但顺序和做法需要调整。** 两个开源项目在"技能系统怎么设计"上高度收敛（SKILL.md 文件格式 + 按需加载 + MCP 统一为技能文档治理），这是可以直接抄作业的部分；但在"严谨问答"上两者都没做到代码级强制 —— 这恰恰是 FormuMind 可以超越它们的差异化机会（你已有的 claim_checker + citation_binder 是它们没有的地基）。

## 2. 两个开源项目分析结论（精炼版）

### 2.1 synthetic-sciences/OpenScience（TS + Bun，本地优先 Agent 工作台）

**值得借鉴（按价值排序）：**
1. **Publication preflight**（`src/file/review.ts`）：引用 blocking 检查（未定义的 `@key`/footnote、`[citation needed]` 占位符）、含数字的行无内联证据判 major、`assertReady` 有 blocking finding 直接抛错阻止发布。这是"钢印引用"最接近代码强制的实现。
2. **Provenance claim 节点**（`src/science/provenance/`）：内容寻址（sha256）的 artifact/run/source/claim 图谱，可把"论断→证据"持久化为可查询节点。
3. **Literature RAG 管线**（`src/research/literature.ts`）：多源扇出（OpenAlex + arXiv）→ DOI/id/标题去重 → **RRF 融合** → 最优 OA 全文 resolve → 页码寻址提取。RRF 实现与去重键设计可直接参考（你正在做 R4 RRF A/B）。
4. **技能系统全套设计**：SKILL.md frontmatter 合约、多源发现与优先级（project > user > installed > bundled）、只索引 frontmatter、按需加载、`allowed_tools` 可见性解锁、安全审查分层。
5. **MCP 配置模型**：local/remote schema、密钥密封存储、每次调用过权限 ask、catalog 预设。
6. **`paper-lookup` 的可重复性信条**："A literature lookup is only as trustworthy as it is repeatable" —— 输出强制带 endpoint/参数/标识符/访问日期。
7. **诚实降级设计**：abstract-only 标注、错误分类降级渲染（与你现有的 `cross_encoder applied=False` 标记同类思路，可统一为"证据诚实度"标注）。
8. **Review 包结构**：review.md / sources.csv / references.bib / search-methods.md —— Wiki 落盘可借鉴为"证据包"。

**不要借鉴：** 它的 RAG 是纯 FTS5+BM25（无向量，CJK 还要 fallback）—— 检索层面它不如你现在的方案；371 技能 bundled 包、Electron 壳等过度设计；聊天回答引用只有 prompt 约束、无代码强制（这正是你要超越的点）。

**License：** 本体 Apache 2.0（可商用/衍生，保留声明即可）；技能是混合许可包，逐 skill 看 frontmatter `license` 字段（Anthropic 文档技能是 "under Anthropic's terms" 非 OSI —— 避开）。

### 2.2 aipoch/open-science（Electron 桌面，v0.33.3）

**值得借鉴（按价值排序）：**
1. **显式加载 explicit loading**（`skills/runtime-mcp-server.ts`）：catalog 只暴露 `name + description`（64KB 预算），`load_skill(skill)` 调用时才读正文。技能/MCP 增多后控制 prompt 膨胀的标准解法。
2. **无状态 LLM 分类器做技能路由**（`settings/chat-skill-selector.ts`）：`temperature: 0`、`max_tokens: 512`、function tool `select_skills(skill_names[0..3])`，15s 超时 + 失败回退。你的"意图解析→技能选择"可用同样模式。
3. **MCP 经"生成的 Skill 文档"统一暴露**（`connectors/skill-doc.ts`）：把 MCP server 的 tools 渲染成 `mcp-<name>/SKILL.md`，agent 侧只有一套技能发现/调用/审批心智模型 —— 统一治理的最简方案（注意它丢弃了 resources/prompts，只暴露 tools）。
4. **逐工具 Allow/Ask/Block + 三级 scope 授予**（session/project/global）：你的"配方执行类工具"（下发实验、采购）可直接套用这套审批语义。
5. **Reviewer turn 级审计**（`reviewer/rubric.ts:215-236` §5.8）："伪造引用无源即定罪"（正文 warn、saved artifact 直接 fail）+ fix-loop 最多 3 轮。Wiki/报告生成可借鉴"生成后审计"而非"生成时强制"。
6. **ArtifactLiteratureManifest**（`artifacts/literature-manifest.ts`）：每次检索的 criteria→命中→count 随 artifact 版本不可变固化 —— "配方报告可追溯到哪次文献检索"可直接照抄数据模型。
7. **Smart screening 结构化 verdict**：专用分类模型（非对话模型）输出 `match|no-match|uncertain` + probabilities + `evidenceIndex`，证据不足直接判 `insufficient-evidence` —— 文献/供应商筛选场景可复用。
8. **SKILL.md 审批卡渲染正文**：审批时给用户看技能文档正文而非 JSON —— 工具审批 UX 可借鉴。
9. **错误脱敏纪律**：凭据/headers/system prompt 禁止回显给 agent —— 可直接做你的安全 checklist。

**不要借鉴：** Agent 循环外包给外部框架（四适配器 + 约 90 个 ACP 编排文件）—— 为"model-agnostic 桌面"服务的重型设计，你的自有后端不需要；Specialist 三层技能限定（多租户设计，过度）；Marketplace 签名体系（初期无分发场景）；Notebook/SSH/Slurm/RO-Crate 等重型科研复现设施。

**License：** 主 Apache-2.0；内置技能 frontmatter 自带第三方许可声明（如 alphafold2 weights CC-BY-4.0）—— 抄技能内容时逐个看。

### 2.3 共同结论

两个项目在技能系统设计上**独立收敛到同一套答案**：
- **SKILL.md + YAML frontmatter = 技能定义标准格式**（name/description 必填，category/requirements/license 可选）
- **catalog 只索引元数据，正文按需加载**（explicit loading，防 prompt 膨胀）
- **MCP 不单独治理，渲染成技能文档统一发现/调用/审批**（心智模型只有一套）
- **用户自定义 = 本地目录 + 导入审批**（Git import / 文件上传），签名/市场等分发设施延后
- **严谨性 = 事后审计 + 证据固化**，而非生成时强制（两者都没做到后者 —— 你的机会）

## 3. 必要性评估

| 提议 | 必要性 | 理由 |
|---|---|---|
| ① 右栏技能坞 → 设置页统一管理 Skills/MCP + 自定义技能 | **高** | 现状：右栏技能坞是静态配方 playbook（`resources/formulation_skills.py` 第一行 docstring 自述 "not MCP agents"），与 chat 不连通、不可配置。配方平台天然需要可扩展的领域能力（新表征方法、新文献源、lab SOP），统一管理是地基。两个开源项目都已验证"设置页 SkillsPanel/ConnectorsPanel"的信息架构。 |
| ② 中栏问答框 `+` 按钮（上传文件 / 选 skills / 选 MCP） | **中高** | 符合 Claude 交互范式，把能力选择权交给用户。现状：输入框只有结构式图片上传（`ResearchPanel.tsx:575`），通用附件能力已存在但没接进来（`api.uploadAttachment` + `attachment_source_ids`）。注意 aipoch 的 composer 里**没有 MCP 独立入口**（MCP 伪装成 `mcp-<name>` skill）—— 你可以做得比它更显式，这是改进点。 |
| ③ PaperQA2 定位：Evidence Synthesis 学术专属 Agent | **最高** | 这是核心差异化。PaperQA2 的三件套：agentic RAG（search/gather_evidence/answer_question 三工具 + agent 自主决定调用顺序）→ 证据门槛（≥5 条多源证据才答题）→ query-aware 打分摘要。你已有的 claim_checker（fail-open）+ citation_binder + golden 评估是它们没有的地基，补上"代码强制"环节即形成超越。 |

## 4. 可行性评估（基于前端现状地图）

**已有的好消息（可直接复用）：**
- 后端已有 OpenAI-compatible tool-calling（`llm.py:646-773`），SSE 已有 `tool_start`/`tool_result` 事件，前端已渲染工具状态 —— 技能/MCP 调用不需要重写流式链路。
- `ChatRequest` 前后端已有 `attachment_source_ids` 字段 —— 通用文件上传进 chat 是"接线"不是"新建"。
- 设置页是硬编码 tab 数组 —— 加一个 `skills-mcp` tab 是小改动。

**真正的工程量（今天完全不存在）：**
- 后端**无技能注册中心**（现有 FormulationSkill 是静态目录、无 SKILL.md 约定、无加载机制）、**无 MCP 客户端**（全仓 grep 零命中）、`api/chat.py` 的 tool loop 是"写死 8 工具" —— 这三处是最大触点。
- 两个"技能"概念（配方 playbook / 化学工具）互不相连，需要新建统一层而非复用。

**风险：**
- MCP 执行层（stdio 子进程）引入进程管理与安全面，一次做全容易失控 —— 建议先做"配置管理 UI + 技能文档统一"，执行层后接（两个开源项目都证明了配置先行是可行路径）。
- 不要复刻 aipoch 的 ACP 多框架适配 —— 你的自有后端不需要那 90 个文件的复杂度。

## 5. 专业升级方案（分四阶段）

### 阶段 1：技能注册中心 + 设置页统一管理（地基，约 2-3 周）

**目标**：SKILL.md 成为 FormuMind 技能的唯一定义格式；设置页新增 "Skills 与 MCP" tab，统一管理内置技能、自定义技能、MCP 服务器。

后端新建 `backend/app/services/skills/`：
- `registry.py`：多源发现（内置 `resources/skills/` → 用户 `~/.formumind/skills/` → 项目级 `.formumind/skills/`），优先级 project > user > builtin；只索引 frontmatter（name/description/tags/category/requirements/license），正文按需加载。
- `loader.py`：`load_skill(name)` —— 读 SKILL.md 全文 + 参数模板替换（`$ARGUMENTS`），做注入行剥离（`always run this skill` 类指令），返回 contentHash（SHA-256）供溯源。
- `router.py`：无状态 LLM 分类器（抄 aipoch `chat-skill-selector.ts`：temperature 0、max_tokens 512、function tool `select_skills(names[0..3])`、15s 超时、失败回退），接在现有意图解析之后。
- `validate.py`：导入安全审查（抄 OpenScience `install/review.ts` Layer1：拒绝 description 注入指令、灾难模式命令；Layer4 警告 `curl|sh` 等）。
- 技能格式直接采用 SKILL.md 约定（含 `third_party` 许可声明字段 —— 对合规场景有价值）。

前端：
- `SettingsModal.tsx` 加 `skills-mcp` tab（`store/types.ts` 联合类型同步）；新建 `SkillsMcpPanel.tsx`（技能列表/启用开关/详情/MCP server 配置表单/自定义技能上传）。
- `ActionSkillsDock.tsx` 按你的提议从右栏迁移 —— 建议**保留为"配方 playbook 快捷入口"**（它管的是 doe_engine/optimize_engine 预设，与 agent skills 不是一回事，硬合并会混淆概念），在右栏改为链接到设置页的技能管理。概念澄清比物理迁移更重要。

后端 `api/settings.py` 加 Skills/MCP CRUD 端点 + 持久化（建议 SQLite 表而非散装 env，参考现有 settings 模式）。

**验收**：设置页可上传一个 SKILL.md（如"盐雾测试解读"），chat 能经分类器自动选用；右栏技能坞概念澄清完成。

### 阶段 2：中栏 `+` 按钮 + 统一调用（约 2 周）

**目标**：问答框 `+` 菜单可选"上传文件 / 技能 / MCP"，选中项随问题发送并真实参与 tool loop。

前端：
- `ResearchPanel.tsx` 输入框旁加 `+` 按钮 + `SkillMcpPicker.tsx` 弹出菜单（读设置中启用的 skills/MCP；比 aipoch 更显式 —— skills 和 MCP 分区展示，不伪装）。
- 上传文件：把已有的 `api.uploadAttachment` 接进输入框（现在只在 LabWorkbench 用），`attachment_source_ids` 随 `ChatRequest` 发送。
- store 加 `selectedSkills` / `selectedMcpServers` / `pendingAttachments` 状态。

参数链路（前后端小改）：
- `ChatRequest` 加 `enabled_skills: list[str]`、`enabled_mcp_servers: list[str]`（前端 `api/types.ts` + 后端 `domain/chat_schemas.py` 同步）。
- `sendChat`（`searchSlice.ts:464`）把选中项组装进 `reqBody`。

后端（大改动，核心触点）：
- `api/chat.py` 的 tool loop 从"写死 8 工具"改为三层合并：**内置化学工具（现有 8 个）+ 用户选中的 skills（经 `load_skill` 注入为工具）+ 选中的 MCP servers（阶段 4 执行层未就绪前，先渲染为 skill 文档走同一调用路径，抄 aipoch `skill-doc.ts` 模式）**。
- SSE 透出 skill/MCP 执行状态（复用现有 `tool_start`/`tool_result` 事件格式，前端零改动）。
- 审批语义：抄 OpenScience 的逐工具 Allow/Ask/Block + session/project/global scope（配方执行类工具如下发实验默认 Ask）。

**验收**：`+` 选技能/MCP 后提问，SSE 流中可见 skill 调用事件，回答引用了技能内容；审批卡渲染 SKILL.md 正文（抄 aipoch 的 UX）。

### 阶段 3：Evidence Synthesis（PaperQA2 定位，约 3-4 周）

**目标**：从"线性 RAG + fail-open claim 检查"升级为"agentic RAG + 代码强制引用门"。

3a. **Agentic RAG 三工具**（对标 PaperQA2，复用你现有的检索栈）：
- `search_literature`：多源扇出（现有 EPO/OpenAlex + 新增 arXiv，抄 synthetic `literature.ts` 的扇出逻辑）→ DOI/id/标题去重 → RRF 融合（你 R4 的 A/B 结论直接用）。
- `gather_evidence`：候选 chunks 做 **query-aware 打分摘要**（summary LLM + 1-10 相关性分，抄 PaperQA2 的 prompt 模式："只做证据摘要、不直接回答；不相关回 Not applicable"）—— 这比 cross-encoder rerank 更进一步：不只排序，还压缩证据。
- `answer_question`：**证据门槛** —— 要求 ≥N 条多源证据才允许生成答案（N 可配，默认 5），不足时 agent 自主决定换措辞重搜（把 PaperQA2 的主 prompt 翻译为中文系统指令）。
- MMR 多样性检索补上（当前 hybrid_search 无多样性控制）。

3b. **Publication preflight**（抄 synthetic `src/file/review.ts`，接在 Wiki/STORM 落盘前）：
- `[^n]` 无对应脚注 → blocking；`[citation needed]`/TODO 占位符 → blocking；含数字/%/单位的行无内联证据 → major。
- `assertReady` 有 blocking finding 则阻止发布（把 claim_checker 从 fail-open 升级为落盘前 fail-closed；chat 问答保持 fail-open + 显式标记，避免误伤交互体验）。

3c. **证据固化**（抄 aipoch `ArtifactLiteratureManifest` 数据模型）：
- 每次文献检索的 criteria（query/源/日期）→ 命中 itemIds → totalCount，随 Wiki page/报告版本不可变存储 —— "配方报告可追溯到哪次文献检索"。
- 输出加可重复性字段（抄 `paper-lookup` 信条：endpoint、参数、标识符、访问日期）。

3d. **Reviewer 审计**（抄 aipoch `reviewer/`，轻量版）：
- Wiki/长报告生成后，独立上下文 reviewer 按 rubric 审计（核心纪律：数字必须在记录中找到来源；伪造引用直接 fail），最多 3 轮 fix-loop。
- 与你现有的 W1（claim 定向再生）是同一闭环的两种实现 —— W1 是生成内循环，reviewer 是生成后外循环，两者互补。

**验收**：Wiki 落盘前 preflight 拦截过至少一次真实缺陷（CI 回归测试覆盖）；证据包（review.md/sources.csv/references.bib/search-methods.md）随 Wiki 落盘；golden 评估加 faithfulness 门禁。

### 阶段 4：MCP 执行层（按需，约 2-3 周）

**目标**：真正的 MCP client（stdio / StreamableHTTP / SSE），补上阶段 2 的"渲染为 skill 文档"的临时方案。

- 新建 `backend/app/services/mcp_client/`：`client-manager.py`（懒连接、按 server id 缓存、`listTools` 分页防游标循环、`call`、OAuth、closeAll），transport 工厂支持 stdio/SSE/StreamableHTTP（`mcp` Python SDK）。
- 配置模型抄 aipoch `StoredCustomMcpServer`：id/name（`^[a-z0-9-]+$`）/transport/command-args-env/url-headers/oauth/enabled/trustedAt；密钥走 Secure Vault（参考现有 `post_secrets_update` 模式），不存明文。
- 安全：每次调用过权限 ask（默认），逐工具 Allow/Ask/Block；stdio 用数组传参（无 shell 拼接）；远程仅 HTTPS/loopback；stderr 脱敏；配置变更期间 fail-closed。
- 注意 aipoch 丢弃了 resources/prompts —— 如果你的场景需要（如 MCP server 暴露配方模板 prompts），补上 `listResources`/`readResource`。

**验收**：接一个真实 MCP server（如文件系统或自建配方 SOP server），`+` 菜单选中后 agent 可调用其工具，全程有审批记录。

## 6. 与现有能力的衔接（一一对应，不另起炉灶）

| 现有能力 | 升级后角色 |
|---|---|
| `claim_checker`（fail-open） | 升级为 preflight（落盘前 fail-closed）+ reviewer 审计的内循环（W1 定向再生） |
| `citation_binder`（`[^n]` 重映射） | 升级为 provenance claim 节点（论断→证据可查询）+ preflight 的引用完整性检查 |
| `rerank_cross_encoder_scored` | 被 `gather_evidence` 的 query-aware 打分摘要增强（排序→排序+压缩） |
| `hybrid_search_scored` + RRF A/B（R4） | 成为 `search_literature` 的融合层；RRF 实现参考 synthetic `literature.ts` |
| golden 53 问 + DeepEval | 加 faithfulness/引用精确率门禁；Wiki 加 deterministic STORM 基线告警（W10） |
| `formulation_skills.py`（静态 playbook） | 保留，概念上明确为"配方工作流预设"；agent skills 走新注册中心；两者在设置页分区展示 |
| 8 个化学工具 | 成为三层 tool 合并的第一层（内置层） |

## 7. 不做清单（两个项目验证过的坑）

1. **不复刻 aipoch 的 ACP 多框架适配**（四适配器 + 90 个编排文件）—— 你的自有后端不需要。
2. **不追求"生成时强制每个结论带引用"** —— 两个项目都没做到；采用"证据固化 + 事后审计 + 落盘前 preflight"三件套，chat 保持 fail-open + 诚实标记。
3. **初期不做 Marketplace 签名分发体系** —— 先本地技能目录 + 导入审批。
4. **不把 MCP resources/prompts 一开始就全暴露** —— 先 tools，resources 按需补。
5. **检索层不退回 BM25-only** —— aipoch 的 FTS5 方案不如你现在的 Qwen3-Embedding + cross-encoder 路线，坚持现有路线。
6. **Specialist 三层技能限定等多租户设计** —— 当前单用户场景过度设计，取白名单思想即可。

## 8. License 合规

- 设计思想借鉴无风险；直接复制代码时：两个项目本体都是 Apache-2.0（保留 LICENSE 头 + 版权声明即可）。
- 例外：synthetic 技能中 Anthropic 文档技能是 "under Anthropic's terms"（非 OSI）—— 避开；aipoch 内置技能 frontmatter 的 `third_party` 声明逐个检查（如 weights CC-BY-4.0）。
- 建议在仓库建 `ATTRIBUTION.md` 登记借鉴来源（抄 synthetic 的合规约定）。

## 9. 里程碑

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| 1（2-3 周） | 技能注册中心 + 设置页统一管理 | 上传 SKILL.md 可被分类器选用；MCP 配置可增删改 |
| 2（2 周） | `+` 按钮 + 三层 tool 合并 | 选中 skill/MCP 真实参与 tool loop；审批卡渲染正文 |
| 3（3-4 周） | Agentic RAG + preflight + 证据固化 + reviewer | preflight 在 CI 拦截真实缺陷；Wiki 带证据包落盘 |
| 4（2-3 周，按需） | MCP 执行层 | 真实 MCP server 可调用，全程审批记录 |

每阶段改后跑全量测试保持全绿，再进入下一阶段。

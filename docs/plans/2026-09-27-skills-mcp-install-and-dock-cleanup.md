# Skills / MCP 进阶：移除技能坞 · 泛化配方推荐 · 一键安装（GitHub / 本地）

> 状态：**Phase A–C 已实现**（坞清理 · Skills 安装 · MCP 导入）  
> 前置：#161 Skills/Evidence/Connectors/MCP 骨架已合入 main  
> 对照：AIPOCH `github-import.ts` / SynSci-OS `skill/install/*` / Claude Science 安装体验  
> 目标：回答三问并给出可落地路径——(1) 右栏技能坞能否移除 (2) 「硅烷偶联推荐」是什么、如何改为泛配方 (3) Skills/MCP 如何支持 GitHub / 本地上传 / 一键安装

---

## 1. 结论速览

| 问题 | 结论 |
|------|------|
| **右栏技能坞可否移除？** | **可以，且建议移除「市场式坞」**。设置页 + 中栏 `+` 已覆盖发现/启停/本轮选用；坞仅保留可选的 **Active Playbook 迷你条**（忙碌时 checklist），或一并并入 PathWizard。 |
| **硅烷偶联推荐是什么？** | 不是引擎本身，而是一条 **硬编码 Playbook 预设**：点一下 → 打开「配方推荐」Modal，并注入硅烷/环氧检索提示。主推荐管线本身按 `ProductDomain` 工作，**被这条预设锁窄了心智模型**。 |
| **应改成什么？** | 默认 Playbook 改为 **「配方推荐」**（域自适应 `search_hint`）；硅烷/环氧等作为 **可选域包 / 可选 Chat Skill**，不再占默认首位。 |
| **GitHub / 本地 / 一键安装？** | **高必要性、可实现**。借鉴 Claude Science / AIPOCH / SynSci：**预览 → 安全审查 → 一键确认安装**；MCP 同步支持「粘贴 JSON / 上传配置 / 目录预设」。 |

---

## 2. 现状与重复入口

```
设置 Settings → Skills / MCP     ← 库存、启停（#161）
中栏 Composer [+]                ← 本轮选用 skills / connectors / Evidence
右栏 ActionSkillsDock            ← 再列一遍 playbook + checklist（冗余）
Actions 按钮（推荐/DOE/寻优…）   ← 真正执行入口
```

| 入口 | 职责 | 评价 |
|------|------|------|
| Settings Skills | 启用/禁用库 | 保留并加强（安装源） |
| Composer `+` | 本轮选用 | 保留；playbook 触发 Modal 已在此 |
| **技能坞** | 浏览 + 应用 playbook + 忙碌 checklist | **浏览/应用与 Settings/+ 重复**；checklist 有独立价值 |
| PathWizard | 配方路径引导 | 可承接「活跃行动包」进度 |

**移除坞的风险与缓解**

| 风险 | 缓解 |
|------|------|
| 用户找不到「一键 DOE」 | Composer `+` → 行动包；Actions 原按钮保留；Settings 说明 |
| 忙碌时看不到 checklist | 可选 **ActivePlaybookStrip**（仅活跃一条 + 三步清单），高度约 1 卡片，不做技能市场 |
| 测试依赖 `ActionSkillsDock` | 迁测到 Strip 或 Composer；删旧组件 |

**建议决策（默认锁定）**

1. **删除** `ActionSkillsDock` 市场式列表。  
2. **可选 MVP**：`ActivePlaybookStrip`（仅 `activeSkillId` 非空时显示）。  
3. Playbook 的「发现」统一到 Settings；「启动」统一到 Composer `+` + Actions。

---

## 3. 「硅烷偶联推荐」解剖与泛化

### 3.1 它是什么

定义见 `backend/app/resources/formulation_skills.py`：

```text
id: silane_recommend
action/modal: recommend          ← 打开现有「配方推荐」Modal
presets:
  prefer_materials_catalog: true
  search_hint: "硅烷偶联剂 环氧 底漆"   ← 仅预设检索话术
```

点击后 `applyFormulationSkill` 会：设 `activeSkillId`、可选改 `prefer_materials_catalog` / `searchQuery`，再 `setOpenModal("recommend")`。

**因此：推荐引擎并未写死硅烷**；写死的是这条 **Playbook 的标题与 search_hint**，以及防腐涂料示例域（`anticorrosion_coating`）的产品叙事惯性。

### 3.2 为何感觉「平台被锁在硅烷」

1. 坞/设置里 **第一条、带星标** 的技能叫「硅烷偶联推荐」。  
2. `deep_literature` 的 hint 仍是「金属表面处理 防腐涂料」。  
3. 默认示例域偏 anticorrosion_coating，新用户会以为主业=硅烷。

### 3.3 改造方案（产品 + 数据）

| 原 ID | 新 ID / 标题 | 行为 |
|-------|--------------|------|
| `silane_recommend` | **`formula_recommend` / 配方推荐** | `action=recommend`；`search_hint` **空或按当前 `ProductDomain` 生成**（如胶粘剂→「结构胶 固化剂」；涂料→域 profile 词） |
| （新）可选 | `silane_coupling_pack`（Chat Skill 或域包） | 仅当用户安装/启用时出现；不进默认首位 |
| `deep_literature` | 保持，改 presets | `search_hint` 改为当前域主题或用户检索框，去掉硬编码「防腐涂料」 |

**兼容**：`GET /api/formulation-skills` 可对旧 id `silane_recommend` 做 **alias → formula_recommend** 一版，避免外部书签断裂。

**域自适应 hint（示意）**

```python
DOMAIN_SEARCH_HINTS = {
    "anticorrosion_coating": "防腐涂料 底漆 缓蚀",
    "structural_adhesive": "结构胶 环氧 固化",
    "sealant": "密封胶 增塑 触变",
    # …
}
# 未命中 → "配方 组分 工艺" 或 requirement 关键词
```

---

## 4. Skills / MCP「像 Claude Science」的安装体验

### 4.1 目标 UX（与 Claude Science / AIPOCH 对齐的信息架构）

```
设置 → Skills
  [+ 添加技能]
    ├─ 从 GitHub 安装     URL 或 owner/repo[/path][@ref]
    ├─ 上传本地包         .zip / 文件夹（含 SKILL.md）
    └─ 粘贴 SKILL.md      快速单文件
  → 预览（name / description / allowed_tools / 风险）
  → [一键安装] 或 [取消]
  → 列表：启用 · 更新 · 卸载 · 来源徽章（bundled / github / local）

设置 → MCP / Connectors
  [+ 添加]
    ├─ 从目录选用预设     （Literature / Chemistry / 社区 JSON）
    ├─ 粘贴 MCP 服务器 JSON（Claude / Cursor 兼容形状）
    ├─ 从 GitHub 拉取 mcp.json / connector 清单
    └─ 本地上传 .json
  → 预览 tools/list（Probe）
  → [一键启用]
```

### 4.2 Skills 安装协议（FormuMind 子集）

**包形状（兼容双源）**

```
my-skill/
  SKILL.md              # 必需 frontmatter: name, description
  references/…          # 可选
  # 禁止：任意可执行二进制；allowed_tools 必须落在白名单
```

**来源**

| 源 | 实现要点 | 借鉴 |
|----|----------|------|
| GitHub | 解析 `https://github.com/org/repo[/tree/ref/path]`；下载目录或 sparse；记录 `pinned_sha` | AIPOCH `github-import.ts`；SynSci `git-fetch` + ledger |
| 本地 zip | 解压到 `data/skills/<name>/`；大小上限（如 2 MiB 文本） | AIPOCH import-limits |
| 粘贴 MD | 写入 `data/skills/<name>/SKILL.md` | 已有 frontmatter 解析 |

**安全闸（安装前，对标 SynSci review）**

1. Frontmatter 必填；`name` 合法 `[a-z0-9\-]+`。  
2. `allowed_tools` ⊆ FormuMind 白名单（已有 `ALLOWED_CHAT_TOOLS`；playbook 另册）。  
3. 拒绝：`shell`、路径穿越、过大文件、疑似 prompt-injection 正则（可抄 SynSci `review.ts` 轻量版）。  
4. **预览页展示告警**；用户确认后才落盘（「一键」= 确认后的单次动作，不是静默）。  
5. 安装账本：`data/skills_install_ledger.json`（url、sha、时间、verdict）。

**API 草案**

```
POST /api/skills/install/github   { url, ref?, path?, dry_run? }
POST /api/skills/install/upload   multipart zip | files
POST /api/skills/install/paste    { name?, markdown }
POST /api/skills/install/confirm  { install_id }   # dry_run 后确认
DELETE /api/skills/installed/{id}
GET  /api/skills/installed
```

`dry_run=true` → 返回预览 + warnings，不写盘；确认后再写——对应「先看再一键装」。

### 4.3 MCP 安装 / 添加

| 源 | 行为 |
|----|------|
| 粘贴 JSON | 兼容 `{ "mcpServers": { "name": { "command", "args", "env" } } }`（Claude Desktop / Cursor 常见） |
| 上传 .json | 同上 |
| GitHub | 拉取仓库内 `mcp.json` / `.cursor/mcp.json` / FormuMind `connectors.json` |
| 目录预设 | 内置 Literature/Chemistry；后续可加「社区精选」只读清单（仍本地启用） |

**流程**：解析 → 规范化 →（可选）Probe `tools/list` → 用户确认 → 写入 prefs（现有 `mcp_servers`）→ 默认 **只读启发式** 不变。

**API 草案**

```
POST /api/connectors/mcp/import      { json | url | dry_run }
POST /api/connectors/mcp/upload      multipart
POST /api/connectors/mcp/confirm     { import_id }
```

### 4.4 明确不做（本阶段）

- 远程 Skills Marketplace 账号体系 / 付费架  
- 自动信任任意 GitHub 可执行 MCP（无确认、无 Probe）  
- 把配方库整库以 MCP 对外暴露  
- 完整移植 AIPOCH 525 技能市场

---

## 5. 分阶段落地

### Phase A — 坞移除 + 配方推荐泛化（0.5–1 周）**【已确认 · 本 PR 实现】**

1. ~~删除 `ActionSkillsDock` 自 `ActionsPanel`；测例迁移/删除。~~ → `ActivePlaybookStrip`  
2. ~~`ActivePlaybookStrip`：仅活跃 playbook + checklist。~~  
3. ~~`silane_recommend` → `formula_recommend`（标题「配方推荐」；hint 域自适应；alias 旧 id）。~~  
4. ~~`deep_literature` hint 去防腐硬编码。~~ → `search_hint_mode=domain_literature`  
5. ~~文案：USER_GUIDE / 设置页说明「行动包在设置管理，中栏 + 启动」。~~

**验收**：右栏无技能市场；新用户默认看到「配方推荐」而非硅烷；推荐 Modal 仍按当前域出候选。

### Phase B — Skills 一键安装（1.5–2 周）**【本 PR 实现】**

1. ~~后端 install 管线（github / zip / paste）+ dry_run + ledger + 安全审查。~~ → `skill_install.py`  
2. ~~Settings Skills UI：`+ 添加` 三入口 + 预览卡片 + 一键安装/卸载。~~  
3. ~~安装后自动出现在 catalog；`origin=github|local`；Composer `+` 可见（若启用）。~~  
4. ~~测试：恶意 `allowed_tools: shell` 被拒；github mock dry_run→install。~~

### Phase C — MCP 导入体验（1–1.5 周）**【本 PR 实现】**

1. ~~JSON / 上传 / GitHub mcp.json 导入 + Probe。~~ → `mcp_import.py`  
2. ~~Settings MCP UI 与 Skills 同构的「添加」菜单。~~  
3. ~~旗标 `mcp_client_enabled` 仍默认关；导入后需显式启用服务器。~~（导入不依赖旗标；Probe 仍需旗标）

### Phase D — 打磨（按需）

- 技能更新（按 pinned_sha 检查 upstream）  
- 域包：`coatings-silane` 作为可选 GitHub 示例包（演示「硅烷」应以外挂包存在）  
- 中栏 `/` slash 列出已安装 skills（对齐 Claude）

---

## 6. 必要性与可实现性

| 项 | 必要性 | 可实现性 | 备注 |
|----|--------|----------|------|
| 移除技能坞 | ★★★★★ | 高 | 去重；半天～1 天 |
| 泛化配方推荐 Playbook | ★★★★★ | 高 | 纯资源/文案 + 小逻辑 |
| Skills GitHub/本地安装 | ★★★★☆ | 中高 | 安全审查是关键路径 |
| MCP JSON/GitHub 导入 | ★★★★☆ | 高 | 建立在 #161 MCP prefs 上 |
| 完整 Marketplace | ★★☆☆☆ | 低 ROI | 不做 |

---

## 7. 架构草图（安装后）

```mermaid
flowchart LR
  UI[Settings Add Menu]
  UI --> Preview[dry_run Preview]
  Preview --> Review[Security Review]
  Review --> Confirm[One-click Confirm]
  Confirm --> Disk["data/skills or mcp prefs"]
  Disk --> Catalog["GET /api/skills"]
  Catalog --> Plus[Composer +]
  Catalog --> Strip[Optional ActivePlaybookStrip]
  Plus --> Actions[Recommend / DOE / Evidence]
```

---

## 8. 建议的立即下一步

1. 产品确认：**坞删除** vs **留 Active Strip**（推荐：删坞 + 可选 Strip）。  
2. 确认默认 Playbook 文案：**「配方推荐」** + 域自适应 hint。  
3. 开 Phase A 实现分支；并行设计 Phase B 预览 UI 稿。  
4. 准备 1 个公开示例 skill 仓库（泛配方 Method 写作），作为安装联调靶。

---

## 附录 — 代码锚点

| 主题 | 路径 |
|------|------|
| 硅烷 Playbook | `backend/app/resources/formulation_skills.py` |
| 活跃清单条（替坞） | `frontend/src/components/ActivePlaybookStrip.tsx` · `ActionsPanel.tsx` |
| Settings Skills/MCP | `SkillsSettingsPanel.tsx` · `ConnectorsSettingsPanel.tsx` |
| Composer + | `ChatComposerPlus.tsx` |
| Chat Skill 加载 | `backend/app/services/chat_skills.py` · `skills_store.py` |
| Skills 安装管线 | `backend/app/services/skill_install.py` · `api/skills.py` (`/install/*`) |
| MCP prefs | `backend/app/services/mcp_client.py` · `api/connectors.py` |
| MCP 导入 | `backend/app/services/mcp_import.py` · `/api/connectors/mcp/import*` |
| AIPOCH GitHub 安装 | `/tmp/aipoch-open-science/src/main/skills/github-import.ts` |
| SynSci 安装审查 | `/tmp/openscience/backend/cli/src/skill/install/` |

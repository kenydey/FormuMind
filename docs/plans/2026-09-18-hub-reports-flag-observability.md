# Hub Reports 灰度可观测（旗标状态→设置跳转）

> 状态：**已实现**（2026-09-18）  
> 前置：#118 灰度冒烟已合入；Settings `EnvFlagsPanel` + `openSettings("env")` 已存在  
> 缺口：Reports 页只写死 `wiki_dossier_report_enabled` 代码名；旗标关时生成 409，用户不知如何开  
> 约束：不改默认旗标；不默认开 LLM；不重写 report 引擎；复用 `getEnvFlags` / `openSettings`

## 0. 结论摘要

| 本 MVP 做 | 不做 |
|-----------|------|
| Reports 拉取并展示 wiki / dossier / report 三旗标 | 自动改 `.env`、默认改 true |
| 任一关键旗标关 →「去设置开启」→ `openSettings("env")` | 新 API / 新 Settings Tab |
| 生成/导出错误文案含 `*_enabled is false` 时同款 CTA | DOCX/PDF 硬冒烟 |

## 1. 决策锁定

1. **观测三键**：`wiki_enabled` · `wiki_project_dossier_enabled` · `wiki_dossier_report_enabled`  
2. **跳转**：`openSettings("env")`（已有「环境变量」Tab）  
3. **不替用户改旗标** — 只引导到 Settings 手拨  
4. 旗标接口失败：降级为未知态，仍保留静态说明  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-18-hub-reports-flag-observability.md` | 本方案 |
| `HubReportsPlaceholderPane.tsx` | 旗标条 + CTA |
| `HubReportsPlaceholderPane.test.tsx` | 关/开态与跳转 |

## 3. 验证

```bash
cd frontend && npm test -- --run \
  src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx
```

- 三旗标 mock 为 false → 见 `hub-reports-flags` 含 ×，点「去设置开启」→ `settingsOpen` + `settingsTab=env`  
- 三旗标 true → 见 ✓，无强制 CTA  
- generate 抛 `wiki_dossier_report_enabled is false` → 错误区仍有设置跳转  

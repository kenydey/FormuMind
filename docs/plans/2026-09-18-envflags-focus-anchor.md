# EnvFlags 锚点定位（Reports CTA → 卷宗/Report 旗标）

> 状态：**已实现**（2026-09-18）  
> 前置：#119 Hub Reports 旗标可观测 +「去设置开启」→ `openSettings("env")`  
> 缺口：跳进环境变量 Tab 后停在列表顶部（检索区），用户仍要翻找 `wiki_project_dossier_*`  
> 约束：不改默认旗标；不自动拨 True；复用 Settings / EnvFlagsPanel

## 0. 结论摘要

| 本 MVP 做 | 不做 |
|-----------|------|
| `openSettings("env", { focusEnvAttr })` | 自动改 `.env` |
| EnvFlags 行 `data-testid` + scrollIntoView + 短暂高亮 | 新 Settings Tab |
| Reports CTA 聚焦首个未开旗标（否则 report） | 改旗标默认值 |

## 1. 决策锁定

1. Store 增 `settingsEnvFocusAttr: string | null`  
2. Reports CTA：`flagsMissing[0] ?? "wiki_dossier_report_enabled"`  
3. 面板加载后滚动到对应行并高亮；超时或离开 env Tab 清除 focus  
4. 既有 `openSettings("deps")` 等单参调用保持兼容  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-18-envflags-focus-anchor.md` | 本方案 |
| `store/types.ts` / `index.ts` / `uiSlice.ts` | focus 字段 + openSettings 扩展 |
| `EnvFlagsPanel.tsx` | 锚点 scroll + 高亮 |
| `HubReportsPlaceholderPane.tsx` | CTA 带 focusAttr |
| 相关 `*.test.ts(x)` | 跳转后 focus / 高亮 |

## 3. 验证

```bash
cd frontend && npm test -- --run \
  src/components/EnvFlagsPanel.test.tsx \
  src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx \
  src/store/openSettingsEnvFocus.test.ts
```

- Reports 旗标关 → CTA → `settingsTab=env` 且 `settingsEnvFocusAttr` 为缺失项  
- EnvFlagsPanel 渲染后目标行带 `data-testid=env-flag-…` 与高亮 class  

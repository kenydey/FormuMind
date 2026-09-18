# Reports 旗标条随 EnvFlags 保存刷新

> 状态：**已实现**（2026-09-18）  
> 前置：#120 Reports CTA → EnvFlags 锚点定位已合入  
> 缺口：`getEnvFlags` 仅在 Reports mount 拉取；设置里拨 True 并保存后，背后 Reports 仍显示 ×  
> 约束：不改默认旗标；不轮询；复用 store 轻量 revision 信号

## 0. 结论摘要

| 本 MVP 做 | 不做 |
|-----------|------|
| `envFlagsRevision` + `bumpEnvFlagsRevision()` | 自动拨 True / 改默认 |
| EnvFlags `postEnvFlags` 成功后 bump | 定时轮询 |
| Reports `useEffect` 依赖 revision 重拉旗标 | 刷新整页 Hub |

## 1. 决策锁定

1. 保存成功才 bump（失败不刷新）  
2. Reports 重拉后：若三旗标齐 → 去掉 CTA、出现「路径已开」；并清掉疑似旗标门禁的旧 error  
3. 可选：同次重拉 export caps（轻量，一并做）  

## 2. 文件清单

| 文件 | 改动 |
|------|------|
| `docs/plans/2026-09-18-reports-flags-refresh-on-env-save.md` | 本方案 |
| `store/types.ts` / `index.ts` / `uiSlice.ts` | revision |
| `EnvFlagsPanel.tsx` | save 后 bump |
| `HubReportsPlaceholderPane.tsx` | 依赖 revision 重拉 |
| 相关 `*.test.ts(x)` | ×→✓ |

## 3. 验证

```bash
cd frontend && npm test -- --run \
  src/components/EnvFlagsPanel.test.tsx \
  src/components/knowledge-hub/HubReportsPlaceholderPane.test.tsx
```

- mock：Reports 先见 CTA；bump 后 `getEnvFlags` 返回全 true → CTA 消失、`flags-ready` 出现  
- EnvFlags save 成功调用 `bumpEnvFlagsRevision`  

# FormuMind 解析/检索档位一键配置 + 扫描表格双通道 — 实施方案

日期: 2026-09-05 · 已评审确认 · 执行记录

## 范围
1. **三档 profile API**: `POST /api/settings/parse-profile {low|mid|high}` — 映射到
   已有 env-flags(注册表全含)+ `pdf_parser`/`rag_backend` env, 复用
   secrets_store.write_env_updates 持久化 + config 缓存失效。GET 返回当前
   档位判定 + 硬件探针可用性(gpu/mineru key/vision)。
2. **前端一键档位卡**: SettingsModal 内 3 卡片(低/中/高)一键应用 + 可用性标注。
3. **hybrid 扫描表格双通道**: MinerU 云关闭时, rapidocr 全文之外, 对表格页
   整页图并行送 `vision_extract.extract_structured_table_from_image`, 结构化
   结果并入输出(保真表格, 非纯 OCR 文字流)。
4. **MinerU-Popo**: 本期只核实 mineru.net 云输出是否已含文档级后处理(跨页
   表格/标题层级), 不引入本地 popo(需 GPU 推理, 仅高配未来选项)。

## 档位映射(全部指向已有底层开关, 不发明新 env)
| 档 | 检索 | 解析 |
|---|---|---|
| low | gpu_enabled=false → bm25_faiss | pdf_parser=auto(hybrid 纯本地); mineru_enabled=false; pdf_ocr=true; rapidocr_enabled=true |
| mid | gpu_enabled=true(auto 降级 pylate→bm25) | 同 low + mineru_enabled=true(云, 需 key) |
| high | gpu_enabled=true + rag_backend=auto | pdf_parser=mineru(本地 GPU magic-pdf); pdf_local_ocr=true; mineru_enabled=false |

## 文件
- backend `app/services/parse_profiles.py`(新增): 档位表 + apply + 探针状态
- backend `app/api/settings.py`: +GET/POST /settings/parse-profile
- backend `app/services/hybrid_parse.py`: 扫描件表格双通道(~40 行)
- frontend `SettingsModal.tsx`: +档位卡区; `api.ts`: +2 函数
- tests: `test_parse_profiles.py` + hybrid 表格通道 mock 测试

## 风险
低配缺 vision key 时表格通道自动跳过(探测降级); 档位应用失败(只读 FS)
仍生效于当前进程并返回提示(沿用 formulation_mode 模式)。

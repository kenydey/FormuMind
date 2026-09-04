# 双语资料分流 + 查询翻译 方案(2026-09-04)

## 0. 问题定义(实测依据)

- 全库 26,266 chunks 中**中文 ~10%**,其余英文;用户提问全中文 → **真实负载是
  中文问 × 中文/英文混合语料**。
- 小样 A/B(800 池)实测:单模型无解——
  - MiniLM(现状):中文问×中文内容**崩坏**(7/10 题被"标准必要专利"噪音劫持);
    跨语(中文问×英文内容)分数 0.3 且召回乱码 chunk(废)。
  - bge-small-zh:中文问×中文 0.70-0.77 精准;跨语 0.52-0.62 主题沾边。
  - e5-multilingual:跨语 0.86 高分但**主题粗匹配**(召回钙钛矿/梯度材料论文,
    非答案级);bge-m3 同类且 2.2GB 内存吃紧。
  - 翻译(人工英译→MiniLM 英文库):与 bge 跨语相当,无碾压。
- **结论**:同语检索各自最优(中文 bge / 英文 MiniLM),跨语靠**查询翻译**,而非
  指望单模型做跨语对齐。多语模型(e5/bge-m3)实测不达答案级,排除。

## 1. 方案架构

```
用户提问(中文/英文/中英混合术语)
   │
   ▼
① 语言检测(轻量, 无新依赖)
   CJK 字符占比 ≥30% → 中文主问; 否则英文。
   (检测置信低 → 标记 unknown, 走降级双查)
   │
   ▼
② 路由决策(bilingual_router)
   ├─ 中文主问:
   │    a) bge-small-zh 编码(查询指令前缀) → 中文子库(zh chunks)
   │    b) LLM 翻译成英文(可选开关) → MiniLM 编码 → 英文子库(en chunks)
   │    双语结果各自 top-k 合并(去重 + 分数归一)
   ├─ 英文主问:
   │    MiniLM 编码 → 英文子库; (术语含中文/牌号 → 追加 bge 查中文库)
   └─ unknown: 原 query 双库全查(现状行为, 只加 lang 过滤)
   │
   ▼
③ kb_index.search_chunks 改造(第一级全库检索)
   现有: 单 query_vec 全量扫描 + comparable_embedding 过滤(不同模型被 skip)
   改造: chunks 按 embedding_model 分组; 各组用对应语言 query_vec 打分;
   跨组合并排序取 top-k。化学实体 boost/牌号展开逻辑保持(逐组应用)。
   │
   ▼
④ 二级精排(不变): build_store().ingest(sources).query —— chat.py L318 /
   kb.py L159 / CRAG(FederatedSearchEngine) 共用, 零改动。
   │
   ▼
⑤ LLM 合成(HyDE 扩展 + LLM 重排, 已有, 不变)
```

**关键洞察**:`document_chunks.embedding_model` 列 + `comparable_embedding` guard
(维度+模型名双校验)现成支持**分模型共存**——中文 chunks 用 bge 嵌入、英文 chunks
保持 MiniLM,互不混算。这比"全库换一个模型"更优(各自语言最优,且英文库**无需重嵌**,
只有中文 ~2.6k chunks 需换 bge,重嵌 ≈ 1 分钟)。

## 2. 文件变更清单

| 文件 | 变更 | 风险 |
|---|---|---|
| `app/services/lang_router.py`(新) | 语言检测(CJK 比率 + 置信) + 路由决策 + 双语合并去重 | 低(纯函数) |
| `app/services/query_translate.py`(新) | LLM 中→英翻译(复用 `llm._call_llm`,3s 超时,失败返回 None → 降级) | 低 |
| `app/services/kb_index.py` | `search_chunks` 按 embedding_model 分组打分;`embedding_model_for(lang)` 选模型 | 中(检索热路径) |
| `app/services/rag.py` | 暴露 `embed_texts_for(lang)`(bge/MiniLM 按语言) | 低 |
| `app/db/chunk_store.py` | 存量回填 lang 列;ingest 增量标注(字符集判定) | 低 |
| `app/config.py` | `kb_bilingual: bool`、`kb_query_translate: bool`、路由阈值 | 低 |
| `scripts/backfill_chunk_lang.py`(新, 一次性) | 26k chunks 语言回填 + 乱码/噪音清单输出(与清洗共用) | 低(只读+UPDATE) |
| `scripts/reembed_zh_chunks.py`(新, 一次性) | 中文 chunks bge 重嵌 + embedding_model 更新 | 中(写 DB,可回滚) |
| chat/kb/CRAG 接入 | `_augment_with_kb`/chat 检索调 router 替代直调 search_chunks | 中 |
| 测试 | lang_router 单测/翻译降级/双语合并去重/search_chunks 分组/既有回归 | — |

## 3. 实施步骤(时间表)

- **D1 数据层**:lang 回填脚本(字符集 + 置信)+ 乱码/噪音 chunk 清单;中文 ~2.6k
  chunks bge 重嵌(≈1-2 分钟, 分批);英文库不动。
- **D2 检索层**:`embedding_model_for(lang)` + `search_chunks` 分组打分改造 +
  lang_router(检测/路由/合并)+ 单测。
- **D3 翻译层**:query_translate(LLM 翻译,降级链)+ 双语合并策略调优 + 单测;
  chat/kb 接入路由。
- **D4 验收回归**:全量测试 + 真实问题验收(中文问×中文命中精准;中文问×英文专利
  经翻译命中;英文问行为不回退)+ 可选:CRAG 检索面接入确认。

## 4. 风险矩阵

| 风险 | 影响 | 缓解 |
|---|---|---|
| 语言标注错误(噪音 chunk 误判) | 检索混入 | 置信阈值 + 乱码/营销页清洗清单先行(两模型都召回"圣德益"页的实证) |
| LLM 翻译延迟/失败(deepseek 慢窗口) | 中文问查不到英文库 | 3s 超时 + 失败自动降级为仅中文库(不劣于现状);开关可关 |
| 中文子库仅 2.6k(收益上限受资料量限) | 部分问题无中文答案 | 期望管理:双语分流保证"有的内容能命中",不造内容;清洗可提升有效密度 |
| 单模型→双模型的内存/启动 | 后端内存增(~300MB bge) | bge-small-zh 878MB 峰值实测可接受;懒加载 + _MODEL_CACHE 复用 |
| search_chunks 热路径分组改造回归 | 英文检索退化 | 英文主问路径行为保持单 query_vec 同现状;测试锁定 |
| CRAG/agent 检索面覆盖不全 | 部分入口未分流 | D4 统一验收;router 做成 build_store 同级公共入口 |

## 5. 明确不做(边界)

- 不做多语嵌入模型(e5/bge-m3)——实测主题粗匹配 + 资源重,不达答案级。
- 不做全库统一换 bge——英文侧 bge 无增益(实测相当),保持 MiniLM 免重嵌。
- 不做中文分词器替换——jieba 已在 BM25 词法层工作。
- 语料清洗作为独立工单(与 lang 回填共享清单, 但本方案只负责"检索对"不含"删数据")。

## 6. 验收标准(可量化)

1. 中文问×中文资料:top-5 命中率 vs 现状 MiniLM(噪音劫持 7/10 → 目标 ≤1/10)。
2. 中文问×英文资料(翻译路径):top-5 中英文相关专利 ≥ 现状 bge 跨语(0.52-0.62 基线)。
3. 英文问×英文资料:top-5 与现状**一致或更优**(无回退)。
4. 翻译失败注入:降级后行为 == 仅中文库模式(无异常、无死等)。

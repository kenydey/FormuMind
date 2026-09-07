# RapidOCR 模型"未预载"报错修复记录

**日期**: 2026-09-07
**影响版本**: `pymupdf4llm==1.28.0` + `rapidocr==3.9.2` / `rapidocr-onnxruntime==1.4.4`
**修复文件**:
- `patchspec/pymupdf4llm/rapidocr-text-det-attr.patch`(补丁固化)
- `scripts/apply_patches.py` / `scripts/apply_patches.sh`(应用脚本)
- `backend/pyproject.toml`(版本 pin)

---

## 一、症状

worker 日志反复出现:

```
WARNING/ForkPoolWorker-2] pdf_local extract failed: RapidOCR_DetOnly: No text_detector available.
```

每次对**扫描版 PDF**(无文本层)执行本地 OCR 时都报此错,导致扫描件全文提取失败,降级为无结果(静默丢弃)。表面像是"模型没预载",实则模型早已加载正常。

## 二、根因

`pymupdf4llm 1.28.0` 的 `RapidOCR_DetOnly`(定义在
`pymupdf4llm/ocr/rapidtess_api.py` 与 `paddletess_api.py`)在检测前检查:

```python
if not hasattr(self, "text_detector") or self.text_detector is None:
    raise RuntimeError("RapidOCR_DetOnly: No text_detector available.")
```

但无论 `rapidocr_onnxruntime 1.x` 还是 `rapidocr >=3.9`,检测器属性名都是
**`text_det`**,不存在 `text_detector`。所以这个 guard **永远为真**,每次 OCR 都抛错。

### 为什么不是"模型未预载"

实测 `rapidocr==3.9.2` 的 `RapidOCR(params=...)` 初始化 3.6s 即完成,ONNX 权重
(`PP-OCRv6_det_small.onnx` / `ch_ppocr_mobile_v2.0_cls_mobile.onnx` /
`PP-OCRv6_rec_small.onnx`)全部随 wheel 分发,无需下载。报错纯粹是**属性名不匹配**。

## 三、为什么不用"升级 pymupdf4llm"方案

试过升级 `pymupdf4llm==1.28.2`(该版新增 `rapidocr_391_backend` 正确用 `text_det`),
但它:

1. 强依赖 `PyMuPDF==1.28.2`;而升级 PyMuPDF 后 `pymupdf4llm.use_layout(True)` 触发
   `import pymupdf.layout`,其中 `_features.so` 链接的是 `libmupdf.so.29.0`,
   而 1.28.2 的 wheel 实际只带 `libmupdf.so.28.2` → `ImportError: libmupdf.so.29.0`。
2. 即版面分析直接崩,比原 bug 更糟。

因此**回退到 1.28.0 稳定版**,改用最小 patch 直接改属性名——语义上等价于 1.28.2
修复该 bug 的方式,但不动 PyMuPDF,不引入 layout 缺失问题。

## 四、修复内容(最小改动)

`text_detector` → `text_det`,共 2 个文件、各 1 处 guard + 1 处调用:

- `rapidtess_api.py`(实际生效路径:tesseract + rapidocr)
- `paddletess_api.py`(防御性:paddleocr 未装,但保持一致)

## 五、固化方式

1. **补丁文件** `patchspec/pymupdf4llm/rapidocr-text-det-attr.patch`
   含完整的 unified diff,可直接 `git apply`。
2. **应用脚本** `scripts/apply_patches.py`(核心)/ `scripts/apply_patches.sh`(包装)
   - 在 `pip install` 之后运行,把 patch 应用到 site-packages
   - **幂等**:已应用的 patch 会被识别并跳过,重复跑不报错不重复
3. **版本 pin** `backend/pyproject.toml` 的 `parse_pro` extra:
   - `rapidocr==3.9.2`(原 `>=3.9.0`)
   - `pymupdf4llm==1.28.0`(原 `>=0.0.17`)
   - `PyMuPDF==1.28.0`(显式声明 pymupdf4llm 1.28.0 的强依赖)

## 六、部署/重建时的步骤

```bash
# 1. 安装(会按 pin 拉到正确版本)
cd backend && pip install -e ".[parse_pro,…]"

# 2. 应用补丁(幂等)
bash scripts/apply_patches.sh

# 3. 验证(应打印 [OK] ... already applied)
.venv/bin/python scripts/apply_patches.py
```

## 七、验证结果

- 扫描件端到端 OCR 实测:正确识别 `Magnesium alloy passivation / Neutral salt spray 720h`
- `pdf_local.extract_pages(content, ocr=True)` 完整链路返回正确 markdown
- 137 个相关测试全绿(含 `test_pdf_local.py` / `test_rapidocr_local.py` / `test_hybrid_parse.py`)
- worker 重启后 0 个 `text_detector` 报错,摄入任务正常

## 八、后续若升级 pymupdf4llm 的注意事项

若未来要升到 `>=1.28.2`,**必须**同时:
1. 确认 `pymupdf.layout` 依赖的 `libmupdf.so` 版本与 wheel 实际携带一致
2. 移除/更新本 patch(1.28.2 已内置 `text_det` 修复,补丁会变成多余或冲突)
3. 重新跑 `test_pdf_local.py` + 扫描件端到端验证
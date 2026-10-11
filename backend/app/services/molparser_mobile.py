"""MolParser-Mobile 轻量 OCSR 适配器（2026-10-11 POC）。

MolParser-Mobile（DP Technology，9.98M 参数）：
- 模型：huggingface.co/UniParser/MolParser-Mobile（~40MB）
- 源码：github.com/dptech-corp/MolParser（pip install -e . + requirements_model.txt）
- CPU：78 张/秒（14 vCPU）；GPU：1520 张/秒
- 准确率：Uni-Parser Bench 0.823，超过 MolScribe（0.417）

定位：低配 CPU 主机的 OCSR tier（chem_vision_backend=auto 时内存 ≤4GB 自动选用）。
当前状态：POC 接口已定义，模型未下载部署，_molparser_mobile_available() 返回 False。
部署步骤见 docstring 底部。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_model = None


def molparser_mobile_available() -> bool:
    """MolParser-Mobile 是否可用（模型已部署时 True）。"""
    try:
        __import__("molparser")
        return True
    except Exception:
        return False


def _get_model():
    global _model
    if _model is None:
        from molparser import MolParser
        _model = MolParser(device="auto")
    return _model


def predict_smiles_molparser_mobile(image_path: str) -> str | None:
    """MolParser-Mobile 识别单张结构图。未部署时返回 None。"""
    if not molparser_mobile_available():
        return None
    try:
        model = _get_model()
        results = model.parse([image_path], rec_only=True)
        if results:
            # E-SMILES → 标准 SMILES
            esmi = getattr(results[0], "esmi", None)
            if esmi:
                from molparser import utils as mutils
                return mutils.postprocess_caption(esmi)
        return None
    except Exception as exc:
        logger.warning("MolParser-Mobile predict failed: %s", exc)
        return None


# 部署步骤（生产主机执行）：
#   1. git clone https://github.com/dptech-corp/MolParser.git
#   2. cd MolParser && pip install -e . && pip install -r requirements_model.txt
#   3. 模型自动从 huggingface.co/UniParser/MolParser-Mobile 下载（~40MB）
#   4. 将 ocsr.py 的 _molparser_mobile_available() 改为调用本模块的 molparser_mobile_available()

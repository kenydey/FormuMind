"""RxnIM 反应图像解析适配器（2026-10-11 POC）。

RxnIM（CYF200127/RxnIM）：反应式图像 → 结构化 JSON（reactant/condition/product SMILES）。
组件：
- Pix2Seq 反应检测器（~432MB）：定位反应、反应物、产物、条件文本
- MolScribe（~140MB）：切分后的单分子 SMILES（复用现有部署）
- EasyOCR（~50MB）：条件文本识别（试剂、溶剂、温度）

CPU：5-30s/图（参考 rxnim.sebland.com 部署：idle RSS ~2.1GB，推理 ~3GB）。

定位：反应式图像解析（与 reaction_parser.py 的文本解析互补）。
当前状态：POC 接口已定义，Pix2Seq 模型未部署，rxnim_available() 返回 False。

部署步骤：
  1. 从 https://huggingface.co/spaces/CYF200127/RxnIM 获取模型
     （rxn/model/model.ckpt ~432MB）
  2. pip install easyocr（~50MB 模型自动下载）
  3. MolScribe 复用现有独立 worker
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def rxnim_available() -> bool:
    """RxnIM 是否可用（Pix2Seq 模型已部署时 True）。"""
    # TODO: 模型部署后检查 checkpoint 存在
    return False


def parse_reaction_image(image_path: str) -> dict:
    """解析反应式图像。返回 {"parsed": bool, "reactants": [...], "products": [...], ...}。

    未部署时返回 {"parsed": False, "reason": "rxnim not deployed"}。
    """
    if not rxnim_available():
        return {"parsed": False, "reason": "rxnim not deployed"}
    try:
        # TODO: Pix2Seq 检测 → 切分 → MolScribe 识别 → EasyOCR 条件
        # 1. Pix2Seq 推理得 bboxes（reaction/reactant/product/condition）
        # 2. 每个分子 crop → predict_smiles_molscribe()
        # 3. 条件区域 → EasyOCR
        return {"parsed": False, "reason": "not implemented"}
    except Exception as exc:
        logger.warning("RxnIM parse failed: %s", exc)
        return {"parsed": False, "reason": str(exc)[:100]}

"""P-3: 页码校准。

利用 PDF 书签（bookmark）数据校准近似页码。
书签不可用时回退到原有估算（fail-open）。
"""
from __future__ import annotations


def calibrate_page_numbers(
    approx_pages: list[int],
    bookmarks: list[dict],
    total_pages: int,
) -> list[int]:
    """P-3: 用书签校准近似页码。

    Args:
        approx_pages: 估算页码列表（如字符均分）
        bookmarks: 书签列表 [{"title": ..., "page": ...}]
        total_pages: 总页数

    Returns:
        校准后的页码列表
    """
    try:
        if not bookmarks or not approx_pages or total_pages <= 0:
            return approx_pages or []

        # 书签按页码排序
        sorted_bm = sorted(bookmarks, key=lambda b: b.get("page", 0))
        if not sorted_bm:
            return approx_pages

        calibrated = []
        for approx in approx_pages:
            # 找到最接近的书签页
            closest = min(
                sorted_bm,
                key=lambda b: abs(b.get("page", 0) - approx),
            )
            bm_page = closest.get("page", approx)
            # 如果估算与书签差距在 2 页内，采用书签页；否则保留估算
            if abs(bm_page - approx) <= 2:
                calibrated.append(bm_page)
            else:
                calibrated.append(approx)

        return calibrated
    except Exception:
        return approx_pages

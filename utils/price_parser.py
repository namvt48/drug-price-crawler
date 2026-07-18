"""Parse chuỗi giá tiếng Việt về int VND.

Ví von: người Việt viết "48.000đ", máy cần số 48000. Hàm này lột bỏ mọi
ký tự không phải số (dấu chấm ngăn nghìn, "đ", "VND", khoảng trắng...).
"""

from __future__ import annotations

import re

_DIGITS = re.compile(r"\d+")


def parse_price(raw: str | int | float | None) -> int:
    """"48.000đ" -> 48000 ; 47100.0 -> 47100 ; None/"" -> 0.

    Quy tắc: gom toàn bộ chữ số trong chuỗi (bỏ dấu ngăn cách nghìn).
    Nếu là số sẵn thì ép int (cắt phần thập phân — VND không có xu).
    """
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        return int(raw)

    text = str(raw).strip()
    if not text:
        return 0

    # Bỏ mọi ký tự trừ chữ số (dấu chấm/phẩy đều là ngăn cách nghìn ở VN).
    digits = "".join(_DIGITS.findall(text))
    return int(digits) if digits else 0


def format_price(value: int) -> str:
    """25000 -> "25.000đ" (dấu chấm ngăn nghìn, hậu tố đ)."""
    if not value:
        return "0đ"
    return f"{value:,}".replace(",", ".") + "đ"

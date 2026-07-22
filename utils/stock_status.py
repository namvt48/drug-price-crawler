"""Chuẩn hóa tín hiệu tồn kho khác nhau từ API/HTML của các website."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from utils.models import StockStatus
from utils.normalizer import strip_accents

_STATUS_KEYS = {
    "availability",
    "inventorystatus",
    "productstatus",
    "salestatus",
    "status",
    "stockstatus",
    "webstock",
}
_OUT_BOOL_KEYS = {"isoutofstock", "outofstock", "soldout", "unavailable"}
_IN_BOOL_KEYS = {
    "available",
    "canbuy",
    "canorder",
    "instock",
    "isavailable",
    "isinstock",
}
_QUANTITY_KEYS = {
    "availablequantity",
    "inventoryquantity",
    "onhand",
    "quantity",
    "quantityavailable",
    "stockonhand",
    "stockquantity",
}
_OUT_TOKENS = (
    "discontinued",
    "hethang",
    "ngungkinhdoanh",
    "outofstock",
    "soldout",
    "tamhethang",
    "unavailable",
    "nothave",
)
_IN_TOKENS = ("available", "conhang", "have", "instock")


def _token(value: object) -> str:
    text = strip_accents(str(value)).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


def _status_from_text(value: object) -> StockStatus:
    token = _token(value)
    if any(marker in token for marker in _OUT_TOKENS):
        return StockStatus.OUT_OF_STOCK
    if any(marker in token for marker in _IN_TOKENS):
        return StockStatus.IN_STOCK
    return StockStatus.UNKNOWN


def _quantity_status(value: object) -> StockStatus:
    if isinstance(value, bool):
        return StockStatus.UNKNOWN
    try:
        quantity = float(value)  # API có thể trả quantity dưới dạng chuỗi "0".
    except (TypeError, ValueError):
        return StockStatus.UNKNOWN
    return StockStatus.IN_STOCK if quantity > 0 else StockStatus.OUT_OF_STOCK


def detect_stock_status(
    raw: Mapping[str, Any] | None = None, *, text: str = ""
) -> StockStatus:
    """Nhận diện từ tín hiệu tồn kho rõ ràng; không suy ra từ giá bằng 0."""
    explicit_signals: list[StockStatus] = []
    quantity_signals: list[StockStatus] = []

    def visit(value: object) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                normalized_key = _token(key)
                if normalized_key in _STATUS_KEYS:
                    if normalized_key == "stockstatus" and isinstance(
                        child, (int, float)
                    ):
                        explicit_signals.append(_quantity_status(child))
                    else:
                        explicit_signals.append(_status_from_text(child))
                elif normalized_key in _OUT_BOOL_KEYS and isinstance(child, bool):
                    explicit_signals.append(
                        StockStatus.OUT_OF_STOCK if child else StockStatus.UNKNOWN
                    )
                elif normalized_key in _IN_BOOL_KEYS and isinstance(child, bool):
                    explicit_signals.append(
                        StockStatus.IN_STOCK if child else StockStatus.OUT_OF_STOCK
                    )
                elif normalized_key in _QUANTITY_KEYS:
                    quantity_signals.append(_quantity_status(child))
                if isinstance(child, (Mapping, list, tuple)):
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    if raw is not None:
        visit(raw)
    if text:
        explicit_signals.append(_status_from_text(text))
    if StockStatus.OUT_OF_STOCK in explicit_signals:
        return StockStatus.OUT_OF_STOCK
    if StockStatus.IN_STOCK in explicit_signals:
        return StockStatus.IN_STOCK
    if StockStatus.OUT_OF_STOCK in quantity_signals:
        return StockStatus.OUT_OF_STOCK
    if StockStatus.IN_STOCK in quantity_signals:
        return StockStatus.IN_STOCK
    return StockStatus.UNKNOWN

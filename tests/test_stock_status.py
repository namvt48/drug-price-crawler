from __future__ import annotations

import pytest

from utils.models import StockStatus
from utils.stock_status import detect_stock_status


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"status": "out_of_stock"}, StockStatus.OUT_OF_STOCK),
        ({"availability": "https://schema.org/OutOfStock"}, StockStatus.OUT_OF_STOCK),
        ({"quantity": 0}, StockStatus.OUT_OF_STOCK),
        ({"quantity": "0"}, StockStatus.OUT_OF_STOCK),
        ({"isAvailable": False}, StockStatus.OUT_OF_STOCK),
        ({"stockStatus": "in_stock"}, StockStatus.IN_STOCK),
        ({"stockStatus": 0}, StockStatus.OUT_OF_STOCK),
        ({"stockStatus": 1}, StockStatus.IN_STOCK),
        ({"web_stock": "HAVE"}, StockStatus.IN_STOCK),
        ({"web_stock": "NOT_HAVE"}, StockStatus.OUT_OF_STOCK),
        ({"quantity": 12}, StockStatus.IN_STOCK),
    ],
)
def test_detects_explicit_stock_signals(raw: dict, expected: StockStatus) -> None:
    assert detect_stock_status(raw) == expected


def test_zero_price_alone_is_not_assumed_out_of_stock() -> None:
    assert detect_stock_status({"price": 0}) == StockStatus.UNKNOWN


def test_out_of_stock_wins_when_nested_payload_has_conflicting_signals() -> None:
    raw = {
        "available": True,
        "inventory": {"stockStatus": "out_of_stock", "quantity": 0},
    }
    assert detect_stock_status(raw) == StockStatus.OUT_OF_STOCK


def test_explicit_in_stock_status_wins_over_zero_quantity() -> None:
    raw = {"stockStatus": 1, "quantityAvailable": 0}
    assert detect_stock_status(raw) == StockStatus.IN_STOCK


def test_detects_vietnamese_html_text() -> None:
    assert detect_stock_status(text="Sản phẩm tạm hết hàng") == StockStatus.OUT_OF_STOCK

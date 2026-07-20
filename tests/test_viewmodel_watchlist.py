"""Watchlist formatting tests for the Tk-free GUI view model."""

from __future__ import annotations

from datetime import datetime

from gui import viewmodel as vm
from utils.models import SourceName, WatchlistItem


def _wi(
    drug_name: str = "Boganic",
    source: SourceName = SourceName.GIATHUOCTOT,
    last_price_vnd: int = 67000,
    last_checked: float = 0.0,
    image_url: str = "",
) -> WatchlistItem:
    return WatchlistItem(
        site_id="giathuoctot",
        product_id="p1",
        source=source,
        drug_name=drug_name,
        search_name=drug_name.lower(),
        last_price_vnd=last_price_vnd,
        last_checked=last_checked,
        image_url=image_url,
    )


class TestFormatWatchlist:
    def test_fresh_status(self) -> None:
        now = datetime.now().timestamp()
        items = [_wi(last_checked=now)]
        result = vm.format_watchlist(items)
        assert len(result) == 1
        assert result[0]["status"] == "fresh"
        assert result[0]["price"] == "67,000đ"
        assert result[0]["drug_name"] == "Boganic"

    def test_stale_status(self) -> None:
        old = datetime.now().timestamp() - 3600
        items = [_wi(last_checked=old)]
        result = vm.format_watchlist(items)
        assert result[0]["status"] == "stale"

    def test_never_status(self) -> None:
        items = [_wi(last_checked=0.0, last_price_vnd=0)]
        result = vm.format_watchlist(items)
        assert result[0]["status"] == "never"
        assert result[0]["price"] == "—"

    def test_multiple_items(self) -> None:
        items = [
            _wi(drug_name="A", source=SourceName.GIATHUOCTOT),
            _wi(drug_name="B", source=SourceName.CHOTHUOC247, last_price_vnd=5000),
        ]
        result = vm.format_watchlist(items)
        assert len(result) == 2
        assert result[0]["drug_name"] == "A"
        assert result[1]["source"] == "ChoThuoc247"

    def test_empty(self) -> None:
        assert vm.format_watchlist([]) == []

    def test_image_url_in_output(self) -> None:
        items = [_wi(image_url="https://img.test/view.jpg")]
        result = vm.format_watchlist(items)
        assert result[0]["image_url"] == "https://img.test/view.jpg"

    def test_image_url_empty_default(self) -> None:
        items = [_wi()]
        result = vm.format_watchlist(items)
        assert result[0]["image_url"] == ""


class TestWatchlistSummary:
    def test_summary(self) -> None:
        now = datetime.now().timestamp()
        items = [
            _wi(drug_name="A", last_price_vnd=1000, last_checked=now),
            _wi(drug_name="B", last_price_vnd=2000, last_checked=now),
            _wi(drug_name="C", last_price_vnd=0, last_checked=0.0),
            _wi(drug_name="D", last_price_vnd=0, last_checked=0.0),
        ]
        summary = vm.watchlist_summary(items)
        assert "4 mục" in summary
        assert "2 đã có giá" in summary
        assert "2 chưa check" in summary

    def test_empty(self) -> None:
        assert vm.watchlist_summary([]) == "0 mục | 0 đã có giá | 0 chưa check"


class TestSortWatchlist:
    def test_sort_by_name_then_source(self) -> None:
        items = [
            _wi(drug_name="Zinc", source=SourceName.CHOTHUOC247),
            _wi(drug_name="Aspirin", source=SourceName.THUOCSI),
            _wi(drug_name="Aspirin", source=SourceName.GIATHUOCTOT),
        ]
        sorted_items = vm.sort_watchlist(items)
        assert sorted_items[0].drug_name == "Aspirin"
        assert sorted_items[0].source == SourceName.GIATHUOCTOT
        assert sorted_items[1].drug_name == "Aspirin"
        assert sorted_items[1].source == SourceName.THUOCSI
        assert sorted_items[2].drug_name == "Zinc"

    def test_empty(self) -> None:
        assert vm.sort_watchlist([]) == []

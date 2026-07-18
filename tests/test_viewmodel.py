"""Tests cho gui.viewmodel — logic GUI thuần (không cần display)."""

from __future__ import annotations

from gui import viewmodel as vm
from utils.models import CatalogItem, DrugPrice, SourceName, WatchlistItem


def _dp(name: str, source: SourceName = SourceName.GIATHUOCTOT, price: int = 1000, display: str = "") -> DrugPrice:
    return DrugPrice(drug_name=name, price_vnd=price, price_display=display, source=source)


def _ci(
    name: str,
    product_id: str = "p1",
    source: SourceName = SourceName.GIATHUOCTOT,
) -> CatalogItem:
    return CatalogItem(product_id=product_id, drug_name=name, source=source)


class TestBuildGroups:
    def test_variants_merged_one_canonical(self) -> None:
        names = [
            "Boganic Nén Bao Đường Traphaco (H/100V)",
            "Boganic bao duong H/5 vi x 20v Traphaco",
        ]
        groups = vm.build_groups(names, aliases={})
        assert len(groups) == 1
        variants = next(iter(groups.values()))
        assert set(variants) == set(names)

    def test_different_products_stay_separate(self) -> None:
        groups = vm.build_groups(
            ["Boganic bao duong Traphaco", "Panadol Extra GSK"], aliases={}
        )
        assert len(groups) == 2

    def test_alias_overrides_auto_grouping(self) -> None:
        names = ["Boganic bao duong Traphaco"]
        aliases = {"boganic bao duong traphaco": "Boganic Chuẩn"}
        groups = vm.build_groups(names, aliases=aliases)
        assert "Boganic Chuẩn" in groups
        assert groups["Boganic Chuẩn"] == names

    def test_empty_names(self) -> None:
        assert vm.build_groups([], aliases={}) == {}


class TestBuildCatalogGroups:
    """Như TestBuildGroups nhưng giữ nguyên CatalogItem (cần source+product_id để
    live-fetch giá — xem engine.fetch_live_prices)."""

    def test_variants_merged_keep_catalog_items(self) -> None:
        items = [
            _ci("Boganic Nén Bao Đường Traphaco (H/100V)", product_id="p1"),
            _ci("Boganic bao duong H/5 vi x 20v Traphaco", product_id="p2"),
        ]
        groups = vm.build_catalog_groups(items, aliases={})
        assert len(groups) == 1
        variants = next(iter(groups.values()))
        assert {v.product_id for v in variants} == {"p1", "p2"}
        assert all(isinstance(v, CatalogItem) for v in variants)

    def test_different_products_stay_separate(self) -> None:
        items = [_ci("Boganic bao duong Traphaco"), _ci("Panadol Extra GSK")]
        groups = vm.build_catalog_groups(items, aliases={})
        assert len(groups) == 2

    def test_alias_overrides_auto_grouping(self) -> None:
        items = [_ci("Boganic bao duong Traphaco", product_id="p1")]
        aliases = {"boganic bao duong traphaco": "Boganic Chuẩn"}
        groups = vm.build_catalog_groups(items, aliases=aliases)
        assert "Boganic Chuẩn" in groups
        assert [v.product_id for v in groups["Boganic Chuẩn"]] == ["p1"]

    def test_same_name_multiple_sources_both_kept(self) -> None:
        """Cùng tên thuốc ở 2 site khác nhau — cả 2 CatalogItem phải còn trong nhóm
        (không bị group_names làm mất do chỉ thao tác trên string)."""
        items = [
            _ci("Boganic", product_id="p1", source=SourceName.GIATHUOCTOT),
            _ci("Boganic", product_id="p2", source=SourceName.CHOTHUOC247),
        ]
        groups = vm.build_catalog_groups(items, aliases={})
        assert len(groups) == 1
        variants = next(iter(groups.values()))
        assert {v.source for v in variants} == {SourceName.GIATHUOCTOT, SourceName.CHOTHUOC247}

    def test_empty_items(self) -> None:
        assert vm.build_catalog_groups([], aliases={}) == {}


class TestSuggest:
    def test_query_filters_case_insensitive(self) -> None:
        groups = {"Boganic Traphaco": ["x"], "Panadol Gsk": ["y"]}
        assert vm.suggest(groups, "boga") == ["Boganic Traphaco"]

    def test_empty_query_returns_all(self) -> None:
        groups = {"A": ["a"], "B": ["b"]}
        assert vm.suggest(groups, "") == ["A", "B"]

    def test_limit(self) -> None:
        groups = {f"Drug{i}": ["v"] for i in range(50)}
        assert len(vm.suggest(groups, "", limit=5)) == 5


class TestCheapest:
    def test_min_positive_price_wins(self) -> None:
        records = [_dp("A", SourceName.GIATHUOCTOT, 2000), _dp("A", SourceName.THUOCSI, 1500)]
        best = vm.cheapest(records)
        assert best is not None
        assert best.price_vnd == 1500

    def test_zero_prices_ignored(self) -> None:
        records = [_dp("A", price=0), _dp("A", SourceName.THUOCSI, 3000)]
        best = vm.cheapest(records)
        assert best is not None
        assert best.price_vnd == 3000

    def test_all_hidden_returns_none(self) -> None:
        assert vm.cheapest([_dp("A", price=0)]) is None
        assert vm.cheapest([]) is None


class TestFormatPrices:
    def test_star_marks_cheapest(self) -> None:
        records = [
            _dp("A", SourceName.GIATHUOCTOT, 2000, "2.000đ"),
            _dp("A", SourceName.THUOCSI, 1500, "1.500đ"),
        ]
        s = vm.format_prices(records)
        assert "★ThuocSi: 1.500đ" in s
        assert "★Giathuoctot" not in s
        assert "; " in s

    def test_price_display_fallback_to_vnd(self) -> None:
        s = vm.format_prices([_dp("A", price=2500)])
        assert "2,500đ" in s

    def test_hidden_price_shows_source_only(self) -> None:
        s = vm.format_prices([_dp("A", price=0)])
        assert s == "Giathuoctot"


class TestCheapestLabel:
    def test_label(self) -> None:
        records = [
            _dp("A", SourceName.GIATHUOCTOT, 2000, "2.000đ"),
            _dp("A", SourceName.THUOCSI, 1500, "1.500đ"),
        ]
        assert vm.cheapest_label(records) == "ThuocSi: 1.500đ"

    def test_all_hidden_empty(self) -> None:
        assert vm.cheapest_label([_dp("A", price=0)]) == ""


class TestMergeSelected:
    def test_flatten(self) -> None:
        selected = {
            "A": [_dp("A"), _dp("A", SourceName.THUOCSI)],
            "B": [_dp("B")],
        }
        merged = vm.merge_selected(selected)
        assert len(merged) == 3

    def test_empty(self) -> None:
        assert vm.merge_selected({}) == []


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
        from datetime import datetime
        now = datetime.now().timestamp()
        items = [_wi(last_checked=now)]
        result = vm.format_watchlist(items)
        assert len(result) == 1
        assert result[0]["status"] == "fresh"
        assert result[0]["price"] == "67,000đ"
        assert result[0]["drug_name"] == "Boganic"

    def test_stale_status(self) -> None:
        from datetime import datetime
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
        from datetime import datetime
        now = datetime.now().timestamp()
        items = [
            _wi(drug_name="A", last_price_vnd=1000, last_checked=now),
            _wi(drug_name="B", last_price_vnd=2000, last_checked=now),
            _wi(drug_name="C", last_price_vnd=0, last_checked=0.0),
            _wi(drug_name="D", last_price_vnd=0, last_checked=0.0),
        ]
        s = vm.watchlist_summary(items)
        assert "4 mục" in s
        assert "2 đã có giá" in s
        assert "2 chưa check" in s

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


class TestSearchSeedFor:
    def test_returns_brand_token(self) -> None:
        assert vm.search_seed_for("Boganic Nén Bao Đường Traphaco (H/100V)") == "boganic"

    def test_different_variant_same_seed(self) -> None:
        seed1 = vm.search_seed_for("Boganic Nén Bao Đường Traphaco (H/100V)")
        seed2 = vm.search_seed_for("Boganic bao duong H/5 vi x 20v Traphaco")
        assert seed1 == seed2 == "boganic"

    def test_empty_name(self) -> None:
        assert vm.search_seed_for("") == ""


class TestResolveGroupForItem:
    def test_finds_group_containing_item(self) -> None:
        item = _ci("Boganic Nén Bao Đường Traphaco (H/100V)", product_id="p1", source=SourceName.GIATHUOCTOT)
        sibling = _ci("Boganic bao duong H/5 vi x 20v Traphaco", product_id="p2", source=SourceName.CHOTHUOC247)
        name, variants = vm.resolve_group_for_item(item, [item, sibling], aliases={})
        assert {v.product_id for v in variants} == {"p1", "p2"}
        assert name

    def test_fallback_when_item_missing_from_candidates(self) -> None:
        item = _ci("Boganic Forte", product_id="p1", source=SourceName.GIATHUOCTOT)
        unrelated = _ci("Panadol Extra", product_id="p9", source=SourceName.THUOCSI)
        name, variants = vm.resolve_group_for_item(item, [unrelated], aliases={})
        assert variants == [item]
        assert name

    def test_alias_applied(self) -> None:
        item = _ci("Boganic bao duong Traphaco", product_id="p1", source=SourceName.GIATHUOCTOT)
        aliases = {"boganic bao duong traphaco": "Boganic Chuẩn"}
        name, variants = vm.resolve_group_for_item(item, [item], aliases=aliases)
        assert name == "Boganic Chuẩn"
        assert variants == [item]


class TestFormatScanSummary:
    def test_under_a_minute(self) -> None:
        s = vm.format_scan_summary(150, 9, 42.0)
        assert s == "Đã scan lại catalog toàn bộ 9 site — 150 mục — mất 42s."

    def test_over_a_minute(self) -> None:
        s = vm.format_scan_summary(3000, 9, 125.0)
        assert "2m05s" in s
        assert "3,000 mục" in s

    def test_zero_count(self) -> None:
        s = vm.format_scan_summary(0, 9, 5.0)
        assert "0 mục" in s
        assert "9 site" in s

    def test_exactly_sixty_seconds_uses_minute_format(self) -> None:
        s = vm.format_scan_summary(10, 9, 60.0)
        assert "1m00s" in s

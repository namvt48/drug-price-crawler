"""Tests cho gui.viewmodel — logic GUI thuần (không cần display)."""

from __future__ import annotations

from gui import viewmodel as vm
from utils.models import CatalogItem, DrugPrice, SourceName


def _dp(
    name: str,
    source: SourceName = SourceName.GIATHUOCTOT,
    price: int = 1000,
    display: str = "",
) -> DrugPrice:
    return DrugPrice(
        drug_name=name, price_vnd=price, price_display=display, source=source
    )


def _ci(
    name: str,
    product_id: str = "p1",
    source: SourceName = SourceName.GIATHUOCTOT,
    master_product_id: str = "",
    source_url: str = "",
) -> CatalogItem:
    return CatalogItem(
        product_id=product_id,
        drug_name=name,
        source=source,
        master_product_id=master_product_id,
        source_url=source_url,
    )


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
    """Gom theo master_product_id (đã gộp sẵn bởi entity-resolution trong
    catalog_master.xlsx) — không fuzzy-match lại như build_groups."""

    def test_same_master_id_merged(self) -> None:
        items = [
            _ci(
                "Boganic Chuẩn",
                product_id="p1",
                source=SourceName.GIATHUOCTOT,
                master_product_id="MP1",
            ),
            _ci(
                "Boganic Chuẩn",
                product_id="p2",
                source=SourceName.CHOTHUOC247,
                master_product_id="MP1",
            ),
        ]
        groups = vm.build_catalog_groups(items, aliases={})
        assert len(groups) == 1
        variants = next(iter(groups.values()))
        assert {v.product_id for v in variants} == {"p1", "p2"}
        assert all(isinstance(v, CatalogItem) for v in variants)

    def test_different_master_ids_stay_separate(self) -> None:
        items = [
            _ci("Boganic Chuẩn", product_id="p1", master_product_id="MP1"),
            _ci("Panadol Extra GSK", product_id="p2", master_product_id="MP2"),
        ]
        groups = vm.build_catalog_groups(items, aliases={})
        assert len(groups) == 2

    def test_alias_overrides_display_name(self) -> None:
        items = [_ci("Boganic Chuẩn", product_id="p1", master_product_id="MP1")]
        aliases = {"boganic chuẩn": "Boganic Alias"}
        groups = vm.build_catalog_groups(items, aliases=aliases)
        assert "Boganic Alias" in groups
        assert [v.product_id for v in groups["Boganic Alias"]] == ["p1"]

    def test_missing_master_id_falls_back_to_drug_name(self) -> None:
        """CatalogItem không có master_product_id (vd dựng thủ công) vẫn gộp hợp lý
        theo drug_name thay vì lỗi hoặc tách rời."""
        items = [
            _ci("Boganic", product_id="p1", source=SourceName.GIATHUOCTOT),
            _ci("Boganic", product_id="p2", source=SourceName.CHOTHUOC247),
        ]
        groups = vm.build_catalog_groups(items, aliases={})
        assert len(groups) == 1
        variants = next(iter(groups.values()))
        assert {v.source for v in variants} == {
            SourceName.GIATHUOCTOT,
            SourceName.CHOTHUOC247,
        }

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
        records = [
            _dp("A", SourceName.GIATHUOCTOT, 2000),
            _dp("A", SourceName.THUOCSI, 1500),
        ]
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

    def test_out_of_stock_price_is_not_eligible_for_cheapest(self) -> None:
        unavailable = DrugPrice(
            drug_name="A",
            source=SourceName.THUOCTOT3MIEN,
            price_vnd=1000,
            price_display="1.000đ",
            stock_status="out_of_stock",
        )
        available = _dp("A", SourceName.GIATHUOCTOT, 2000, "2.000đ")

        assert vm.cheapest([unavailable, available]) is available
        assert vm.cheapest([unavailable]) is None


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

    def test_out_of_stock_keeps_reference_price_without_best_marker(self) -> None:
        record = DrugPrice(
            drug_name="Alaxan",
            source=SourceName.THUOCTOT3MIEN,
            price_vnd=109900,
            price_display="109.900đ",
            stock_status="out_of_stock",
        )

        assert vm.format_prices([record]) == "ThuocTot3Mien: hết hàng · 109.900đ"


def _site(name: str, source: SourceName) -> vm.SiteDescriptor:
    return {"name": name, "source": source}


class TestPriceCellsBySource:
    def test_shows_all_sites_not_just_ones_with_records(self) -> None:
        sites = [
            _site("Giathuoctot", SourceName.GIATHUOCTOT),
            _site("ChoThuoc247", SourceName.CHOTHUOC247),
        ]
        items = [
            _ci("A", source=SourceName.GIATHUOCTOT),
            _ci("A", source=SourceName.CHOTHUOC247),
        ]
        records = [_dp("A", SourceName.GIATHUOCTOT, 2000, "2.000đ")]
        cells = vm.price_cells_by_source(sites, items, records)
        assert cells == ["★ Tốt nhất · 2.000đ", "! Lỗi giá"]

    def test_cheapest_starred(self) -> None:
        sites = [
            _site("Giathuoctot", SourceName.GIATHUOCTOT),
            _site("ThuocSi", SourceName.THUOCSI),
        ]
        items = [
            _ci("A", source=SourceName.GIATHUOCTOT),
            _ci("A", source=SourceName.THUOCSI),
        ]
        records = [
            _dp("A", SourceName.GIATHUOCTOT, 2000, "2.000đ"),
            _dp("A", SourceName.THUOCSI, 1500, "1.500đ"),
        ]
        cells = vm.price_cells_by_source(sites, items, records)
        assert cells == ["Giá · 2.000đ", "★ Tốt nhất · 1.500đ"]

    def test_not_in_group_shows_no_product(self) -> None:
        sites = [_site("ThuocHaPu", SourceName.THUOCHAPU)]
        cells = vm.price_cells_by_source(sites, items=[], records=[])
        assert cells == ["— Không có SP"]

    def test_hidden_price_shows_placeholder(self) -> None:
        sites = [_site("Giathuoctot", SourceName.GIATHUOCTOT)]
        items = [_ci("A", source=SourceName.GIATHUOCTOT)]
        records = [_dp("A", SourceName.GIATHUOCTOT, price=0)]
        cells = vm.price_cells_by_source(sites, items, records)
        assert cells == ["! Giá ẩn"]

    def test_out_of_stock_is_not_reported_as_price_error_or_hidden(self) -> None:
        sites = [_site("ThuocTot3Mien", SourceName.THUOCTOT3MIEN)]
        items = [_ci("Colchicin", source=SourceName.THUOCTOT3MIEN)]
        records = [
            DrugPrice(
                drug_name="Colchicin",
                source=SourceName.THUOCTOT3MIEN,
                price_vnd=0,
                price_display="0đ",
                stock_status="out_of_stock",
            )
        ]

        assert vm.price_cells_by_source(sites, items, records) == ["× Hết hàng"]

    def test_out_of_stock_keeps_price_in_main_table(self) -> None:
        sites = [_site("ThuocTot3Mien", SourceName.THUOCTOT3MIEN)]
        items = [_ci("Alaxan", source=SourceName.THUOCTOT3MIEN)]
        records = [
            DrugPrice(
                drug_name="Alaxan",
                source=SourceName.THUOCTOT3MIEN,
                price_vnd=109900,
                price_display="109.900đ",
                stock_status="out_of_stock",
            )
        ]

        assert vm.price_cells_by_source(sites, items, records) == [
            "× Hết hàng · 109.900đ"
        ]

    def test_empty_sites(self) -> None:
        assert vm.price_cells_by_source([], [], []) == []


class TestStatusPresentation:
    def test_five_requested_states_have_distinct_text_and_semantic_kinds(self) -> None:
        cases = {
            "★1.500đ": ("★ Tốt nhất · 1.500đ", "best"),
            "2.000đ": ("Giá · 2.000đ", "price"),
            "lỗi giá": ("! Lỗi giá", "error"),
            "không có SP": ("— Không có SP", "missing"),
            "hết hàng · 109.900đ": ("× Hết hàng · 109.900đ", "out"),
        }

        for raw, (display, kind) in cases.items():
            assert vm.price_cell_display(raw) == display
            assert vm.status_kind(display) == kind


class TestProductDetailRows:
    def test_priced_site_has_price_and_good_status(self) -> None:
        sites = [_site("Giathuoctot", SourceName.GIATHUOCTOT)]
        items = [
            _ci("A", source=SourceName.GIATHUOCTOT, source_url="https://catalog.test/a")
        ]
        rec = DrugPrice(
            drug_name="A",
            price_vnd=2000,
            price_display="2.000đ",
            source=SourceName.GIATHUOCTOT,
            source_url="https://crawled.test/a",
        )
        rows = vm.product_detail_rows(sites, items, [rec])
        assert len(rows) == 1
        row = rows[0]
        assert row["site"] == "Giathuoctot"
        assert row["price"] == "★2.000đ"
        assert row["status"] == "Tốt nhất"
        assert row["updated"] != "—"
        assert "manufacturer" not in row

    def test_url_always_comes_from_catalog_not_crawled_record(self) -> None:
        """Link PHẢI lấy từ CatalogItem (catalog), KHÔNG lấy từ DrugPrice —
        2 URL khác nhau trong test này để phân biệt rõ nguồn nào thắng."""
        sites = [_site("Giathuoctot", SourceName.GIATHUOCTOT)]
        items = [
            _ci("A", source=SourceName.GIATHUOCTOT, source_url="https://catalog.test/a")
        ]
        rec = DrugPrice(
            drug_name="A",
            price_vnd=2000,
            source=SourceName.GIATHUOCTOT,
            source_url="https://crawled.test/a",
        )
        rows = vm.product_detail_rows(sites, items, [rec])
        assert rows[0]["url"] == "https://catalog.test/a"

    def test_url_shown_even_without_any_crawl_record(self) -> None:
        """Yêu cầu chính: link hiện NGAY từ catalog, không cần crawl live
        thành công (khác trước đây phải có DrugPrice mới có link)."""
        sites = [_site("Giathuoctot", SourceName.GIATHUOCTOT)]
        items = [
            _ci("A", source=SourceName.GIATHUOCTOT, source_url="https://catalog.test/a")
        ]
        rows = vm.product_detail_rows(sites, items, records=[])
        row = rows[0]
        assert row["url"] == "https://catalog.test/a"
        assert row["price"] == "—"
        # records rỗng hoàn toàn = CHƯA crawl lần nào, không phải crawl rồi lỗi.
        assert row["status"] == "Chưa cập nhật"

    def test_status_stays_loi_gia_when_other_sites_did_crawl(self) -> None:
        """Đã crawl thật (site khác có record) nhưng site NÀY vẫn thiếu → giữ
        'lỗi giá' (khác hẳn 'chưa update' — có thử mà lỗi, không phải chưa thử)."""
        sites = [
            _site("Giathuoctot", SourceName.GIATHUOCTOT),
            _site("ThuocSi", SourceName.THUOCSI),
        ]
        items = [
            _ci("A", source=SourceName.GIATHUOCTOT),
            _ci("A", source=SourceName.THUOCSI),
        ]
        records = [
            _dp("A", SourceName.GIATHUOCTOT, 2000, "2.000đ")
        ]  # ThuocSi không có record
        rows = vm.product_detail_rows(sites, items, records)
        by_site = {r["site"]: r for r in rows}
        assert by_site["ThuocSi"]["status"] == "Lỗi giá"

    def test_no_catalog_listing_shows_dash_fields(self) -> None:
        sites = [_site("ThuocHaPu", SourceName.THUOCHAPU)]
        rows = vm.product_detail_rows(sites, items=[], records=[])
        assert rows == [
            {
                "site": "ThuocHaPu",
                "price": "—",
                "status": "Không có SP",
                "updated": "—",
                "url": "—",
            }
        ]

    def test_hidden_price_status(self) -> None:
        sites = [_site("Giathuoctot", SourceName.GIATHUOCTOT)]
        items = [
            _ci("A", source=SourceName.GIATHUOCTOT, source_url="https://catalog.test/a")
        ]
        records = [_dp("A", SourceName.GIATHUOCTOT, price=0)]
        rows = vm.product_detail_rows(sites, items, records)
        row = rows[0]
        assert row["price"] == "—"
        assert row["status"] == "Giá ẩn"
        assert row["url"] == "https://catalog.test/a"  # link vẫn có dù giá ẩn

    def test_out_of_stock_status(self) -> None:
        sites = [_site("ThuocTot3Mien", SourceName.THUOCTOT3MIEN)]
        items = [
            _ci(
                "Colchicin",
                source=SourceName.THUOCTOT3MIEN,
                source_url="https://thuoctot3mien.vn/san-pham/4079",
            )
        ]
        records = [
            DrugPrice(
                drug_name="Colchicin",
                source=SourceName.THUOCTOT3MIEN,
                price_vnd=0,
                stock_status="out_of_stock",
            )
        ]

        row = vm.product_detail_rows(sites, items, records)[0]

        assert row["price"] == "—"
        assert row["status"] == "Hết hàng"

    def test_out_of_stock_price_remains_visible_in_detail(self) -> None:
        sites = [_site("ThuocTot3Mien", SourceName.THUOCTOT3MIEN)]
        items = [
            _ci(
                "Alaxan",
                source=SourceName.THUOCTOT3MIEN,
                source_url="https://thuoctot3mien.vn/alaxan-p113.html",
            )
        ]
        records = [
            DrugPrice(
                drug_name="Alaxan",
                source=SourceName.THUOCTOT3MIEN,
                price_vnd=109900,
                price_display="109.900đ",
                stock_status="out_of_stock",
            )
        ]

        row = vm.product_detail_rows(sites, items, records)[0]

        assert row["price"] == "109.900đ"
        assert row["status"] == "Hết hàng"

    def test_detail_splits_numeric_price_from_explicit_status(self) -> None:
        """Bảng chính gộp nghĩa vào ô; bảng chi tiết tách giá và trạng thái."""
        sites = [
            _site("Giathuoctot", SourceName.GIATHUOCTOT),
            _site("ThuocSi", SourceName.THUOCSI),
        ]
        items = [
            _ci("A", source=SourceName.GIATHUOCTOT),
            _ci("A", source=SourceName.THUOCSI),
        ]
        records = [
            _dp("A", SourceName.GIATHUOCTOT, 2000, "2.000đ"),
            _dp("A", SourceName.THUOCSI, 1500, "1.500đ"),
        ]
        rows = vm.product_detail_rows(sites, items, records)
        assert [r["price"] for r in rows] == ["2.000đ", "★1.500đ"]
        assert [r["status"] for r in rows] == ["Có giá", "Tốt nhất"]

    def test_empty_sites(self) -> None:
        assert vm.product_detail_rows([], [], []) == []


class TestCheapestLabel:
    def test_label(self) -> None:
        records = [
            _dp("A", SourceName.GIATHUOCTOT, 2000, "2.000đ"),
            _dp("A", SourceName.THUOCSI, 1500, "1.500đ"),
        ]
        assert vm.cheapest_label(records) == "★ ThuocSi · 1.500đ"


class TestReconcileRecordsWithCatalog:
    def test_removes_legacy_name_search_results_and_requests_refresh(self) -> None:
        items = [
            _ci("Biotin", source=SourceName.THUOCTOT3MIEN),
            CatalogItem(
                product_id="646",
                drug_name="Biotin",
                source=SourceName.THUOCTOT3MIEN,
            ),
        ]
        items[0].product_id = "645"
        exact = DrugPrice(
            drug_name="Biotin 5mg",
            source=SourceName.THUOCTOT3MIEN,
            product_id="646",
            stock_status="out_of_stock",
        )
        stale_similar_name = DrugPrice(
            drug_name="Biotin gần giống",
            source=SourceName.THUOCTOT3MIEN,
            product_id="999",
            price_vnd=10000,
        )

        records, needs_refresh = vm.reconcile_records_with_items(
            items, [stale_similar_name, exact]
        )

        assert records == [exact]
        assert needs_refresh is True

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

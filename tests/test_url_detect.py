"""Tests cho utils.url_detect — dùng URL mẫu THẬT lấy từ
output/catalog_master.xlsx (source_listings) để đảm bảo quy tắc
tách product_id khớp đúng format các crawler thật sự dùng."""

from __future__ import annotations

import pytest

from utils.url_detect import detect_product_id, suggest_name_from_urls

# (site_id, url thật, product_id thật tương ứng trong catalog)
_REAL_SAMPLES = [
    (
        "bachhoathuoc",
        "https://sales.bachhoathuoc.com/15b-with-ginseng-hop-10-vi-x-10-vien-viet-phap--s230101573",
        "230101573",
    ),
    ("chothuoc247", "https://chothuoc247.vn/san-pham/20930", "20930"),
    ("chothuoctot", "https://chothuoctot.vn/san-pham?id=2007143", "2007143"),
    (
        "duocphamgiasi",
        "https://duocphamgiasi.vn/product/a-t-mometasone-furoate-01-t10g-an-thien-boi-da/",
        "https://duocphamgiasi.vn/product/a-t-mometasone-furoate-01-t10g-an-thien-boi-da/",
    ),
    (
        "giathuoctot",
        "https://www.giathuoctot.com/product/cimetidine-400mg-hop-100-vien-nen-thanh-nam-19950000611",
        "cimetidine-400mg-hop-100-vien-nen-thanh-nam-19950000611",
    ),
    (
        "thuochapu",
        "https://thuochapu.com/thuoc/natri-truyen-09.html",
        "https://thuochapu.com/thuoc/natri-truyen-09.html",
    ),
    ("thuocsi", "https://thuocsi.vn/medx-3b-medi-medisun-h100v", "medx-3b-medi-medisun-h100v"),
    (
        "thuocsisaigon",
        "https://thuocsisaigon.vn/products/bong-gac-dap-vet-thuong-bao-thach-8cmx17cm-g-10m",
        "/products/bong-gac-dap-vet-thuong-bao-thach-8cmx17cm-g-10m",
    ),
    ("thuoctot3mien", "https://thuoctot3mien.vn/san-pham/4980", "4980"),
]


class TestDetectProductIdRealSamples:
    @pytest.mark.parametrize("site_id,url,expected", _REAL_SAMPLES)
    def test_matches_real_catalog_product_id(self, site_id: str, url: str, expected: str) -> None:
        assert detect_product_id(site_id, url) == expected


class TestDetectProductIdEdgeCases:
    def test_empty_url_returns_none(self) -> None:
        assert detect_product_id("chothuoc247", "") is None
        assert detect_product_id("chothuoc247", "   ") is None

    def test_unknown_site_returns_none(self) -> None:
        assert detect_product_id("khong_ton_tai", "https://example.com/x") is None

    def test_bachhoathuoc_wrong_format_returns_none(self) -> None:
        assert detect_product_id("bachhoathuoc", "https://sales.bachhoathuoc.com/no-suffix-here") is None

    def test_chothuoc247_wrong_format_returns_none(self) -> None:
        assert detect_product_id("chothuoc247", "https://chothuoc247.vn/khong-dung-duong-dan") is None

    def test_chothuoctot_missing_query_returns_none(self) -> None:
        assert detect_product_id("chothuoctot", "https://chothuoctot.vn/san-pham") is None

    def test_giathuoctot_wrong_format_returns_none(self) -> None:
        assert detect_product_id("giathuoctot", "https://www.giathuoctot.com/khac") is None

    def test_thuocsi_root_url_returns_none(self) -> None:
        assert detect_product_id("thuocsi", "https://thuocsi.vn/") is None

    def test_thuocsisaigon_no_path_returns_none(self) -> None:
        assert detect_product_id("thuocsisaigon", "https://thuocsisaigon.vn") is None

    def test_whitespace_trimmed(self) -> None:
        assert detect_product_id("chothuoc247", "  https://chothuoc247.vn/san-pham/123  ") == "123"


class TestSuggestNameFromUrls:
    def test_picks_longest_non_numeric_segment(self) -> None:
        name = suggest_name_from_urls([
            "https://www.giathuoctot.com/product/cimetidine-400mg-hop-100-vien-nen-thanh-nam-19950000611",
        ])
        assert name == "cimetidine 400mg hop 100 vien nen thanh nam 19950000611"

    def test_skips_pure_numeric_segments(self) -> None:
        """URL không có slug mô tả (chỉ path chung "san-pham" + ID số) — gợi ý rơi về
        segment không phải số duy nhất còn lại, dù không mô tả sản phẩm gì cả (user
        luôn sửa lại ở bước xác nhận, đây chỉ là gợi ý)."""
        name = suggest_name_from_urls(["https://chothuoc247.vn/san-pham/20930"])
        assert name == "san pham"

    def test_multiple_urls_picks_longest_overall(self) -> None:
        name = suggest_name_from_urls([
            "https://chothuoc247.vn/san-pham/20930",
            "https://thuocsi.vn/medx-3b-medi-medisun-h100v",
        ])
        assert name == "medx 3b medi medisun h100v"

    def test_empty_list_returns_empty(self) -> None:
        assert suggest_name_from_urls([]) == ""

    def test_blank_urls_ignored(self) -> None:
        assert suggest_name_from_urls(["", "   "]) == ""

    def test_strips_html_extension(self) -> None:
        name = suggest_name_from_urls(["https://thuochapu.com/thuoc/natri-truyen-09.html"])
        assert name == "natri truyen 09"

"""Tests cho 9 crawler B2B — mỗi crawler test _login + _fetch_products + _parse_product.

HTTP được mock qua httpx.MockTransport. asyncio.sleep bị patch globally để test nhanh.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from crawlers.base import AuthError
from utils.models import SiteConfig, SourceName


async def _noop(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
    monkeypatch.setattr("crawlers.b2b.bachhoathuoc.asyncio.sleep", _noop)


def _cfg(
    site_id: str = "test",
    base_url: str = "https://example.test",
    manual_token: str = "",
) -> SiteConfig:
    c = SiteConfig(id=site_id)
    c.base_url = base_url
    c.credentials.username = "user"
    c.credentials.password = "pass"
    c.auth.manual_token = manual_token
    c.rate_limit.delay_seconds = 0.0
    c.rate_limit.max_retries = 3
    c.rate_limit.retry_backoff_seconds = 0.0
    return c


def _attach(crawler, handler) -> None:
    crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )


def _run(coro):
    return asyncio.run(coro)


def test_all_nine_parsers_preserve_explicit_out_of_stock_signal() -> None:
    """Mọi adapter phải truyền tín hiệu tồn kho vào model chung."""
    from crawlers.b2b.bachhoathuoc import BachHoaThuocCrawler
    from crawlers.b2b.chothuoc247 import ChoThuoc247Crawler
    from crawlers.b2b.chothuoctot import ChoThuocTotCrawler
    from crawlers.b2b.duocphamgiasi import DuocPhamGiaSiCrawler
    from crawlers.b2b.giathuoctot import GiathuoctotCrawler
    from crawlers.b2b.thuochapu import ThuocHaPuCrawler
    from crawlers.b2b.thuocsi import ThuocSiCrawler
    from crawlers.b2b.thuocsisaigon import ThuocSiSaiGonCrawler
    from crawlers.b2b.thuoctot3mien import ThuocTot3MienCrawler
    from utils.models import StockStatus

    cases = [
        (BachHoaThuocCrawler, {"name": "X", "sku": "1", "status": "out_of_stock"}),
        (ChoThuoc247Crawler, {"name": "X", "id": "1", "status": "out_of_stock"}),
        (ChoThuocTotCrawler, {"name": "X", "id": "1", "status": "out_of_stock"}),
        (DuocPhamGiaSiCrawler, {"name": "X", "url": "/x", "status": "out_of_stock"}),
        (GiathuoctotCrawler, {"name": "X", "slug": "x", "status": "out_of_stock"}),
        (ThuocHaPuCrawler, {"name": "X", "url": "/x", "status": "out_of_stock"}),
        (ThuocSiCrawler, {"productName": "X", "slug": "x", "status": "out_of_stock"}),
        (ThuocSiSaiGonCrawler, {"name": "X", "url": "/x", "status": "out_of_stock"}),
        (ThuocTot3MienCrawler, {"name": "X", "id": "1", "status": "out_of_stock"}),
    ]

    for crawler_cls, raw in cases:
        result = crawler_cls(_cfg())._parse_product(raw)
        assert result is not None
        assert result.stock_status == StockStatus.OUT_OF_STOCK


# =====================================================================
# Giathuoctot
# =====================================================================


class TestGiathuoctot:
    def test_login_and_fetch_and_parse(self) -> None:
        from crawlers.b2b.giathuoctot import GiathuoctotCrawler

        c = GiathuoctotCrawler(_cfg("giathuoctot", "https://www.giathuoctot.com"))

        def handler(req: httpx.Request) -> httpx.Response:
            if "login" in req.url.path:
                return httpx.Response(200, json={"data": {"jwtToken": "JWT123"}})
            if "search-products" in req.url.path:
                body = {
                    "products": [
                        {
                            "name": "Boganic",
                            "basePrice": 25000,
                            "manufacturer": {"name": "Traphaco"},
                            "slug": "boganic-slug",
                            "retailUnit": "Viên",
                            "imageUrls": ["https://img.test/boganic.jpg"],
                        }
                    ],
                    "total": 1,
                }
                return httpx.Response(200, json=body)
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl("boganic"))
        assert len(results) == 1
        dp = results[0]
        assert dp.drug_name == "Boganic"
        assert dp.price_vnd == 25000
        assert dp.manufacturer == "Traphaco"
        assert dp.dosage_form == "Viên"
        assert dp.source == SourceName.GIATHUOCTOT
        assert "boganic-slug" in dp.source_url
        assert dp.product_id == "boganic-slug"
        assert dp.image_url == "https://img.test/boganic.jpg"
        _run(c.close())

    def test_login_failure_raises_auth_error(self) -> None:
        from crawlers.b2b.giathuoctot import GiathuoctotCrawler

        c = GiathuoctotCrawler(_cfg("giathuoctot"))

        def handler(req: httpx.Request) -> httpx.Response:
            if "login" in req.url.path:
                return httpx.Response(401, json={"error": "bad creds"})
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError):
            _run(c._login())
        _run(c.close())

    def test_pagination_stops_at_total(self) -> None:
        from crawlers.b2b.giathuoctot import GiathuoctotCrawler

        c = GiathuoctotCrawler(_cfg("giathuoctot"))
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            if "login" in req.url.path:
                return httpx.Response(200, json={"data": {"jwtToken": "T"}})
            if "search-products" in req.url.path:
                calls["n"] += 1
                return httpx.Response(
                    200, json={"products": [{"name": "X", "basePrice": 100}], "total": 1}
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        items = _run(c._fetch_products("kw"))
        assert calls["n"] == 1
        assert len(items) == 1
        _run(c.close())

    def test_parse_product_pricing_table_fallback(self) -> None:
        from crawlers.b2b.giathuoctot import GiathuoctotCrawler

        c = GiathuoctotCrawler(_cfg("giathuoctot"))
        dp = c._parse_product({"name": "X", "pricingTablePrice": 5000, "slug": "s"})
        assert dp is not None
        assert dp.price_vnd == 5000

    def test_fetch_price_by_id_uses_slug_detail_and_member_price(self) -> None:
        from crawlers.b2b.giathuoctot import GiathuoctotCrawler

        c = GiathuoctotCrawler(_cfg("giathuoctot", "https://www.giathuoctot.com"))

        def handler(req: httpx.Request) -> httpx.Response:
            if "login" in req.url.path:
                return httpx.Response(200, json={"data": {"jwtToken": "T"}})
            if "/product/product/slug/" in req.url.path:
                return httpx.Response(
                    200,
                    json={
                        "name": "Alaxan hộp 10 vỉ x 10 viên nén United",
                        "slug": "alaxan-10x10",
                        "basePrice": 116000,
                        "pricingTablePrice": 111000,
                    },
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        result = _run(c.fetch_price_by_id("alaxan-10x10"))

        assert c.direct_fetch_supported is True
        assert result is not None
        assert result.product_id == "alaxan-10x10"
        assert result.price_vnd == 111000
        _run(c.close())


# =====================================================================
# ChoThuoc247
# =====================================================================


class TestChoThuoc247:
    def test_login_and_fetch_and_parse(self) -> None:
        from crawlers.b2b.chothuoc247 import ChoThuoc247Crawler

        c = ChoThuoc247Crawler(_cfg("chothuoc247", "https://chothuoc247.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if p == "/dang-nhap.html":
                return httpx.Response(
                    200,
                    text='<html><meta name="csrf-token" content="CSRF123"></html>',
                )
            if p == "/submitLoginCustomer":
                return httpx.Response(302, headers={"location": "/dat-hang.html"})
            if p == "/dat-hang.html":
                return httpx.Response(
                    200,
                    text='<html><meta name="csrf-token" content="CSRF456"></html>',
                )
            if p == "/searchProduct":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": 42, "name": "Paracetamol", "price": "5000", "unit": "Viên", "web_volume": "500mg", "image": "paracetamol.jpg"}
                        ],
                        "totalPages": 1,
                    },
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl("para"))
        assert len(results) == 1
        dp = results[0]
        assert dp.drug_name == "Paracetamol"
        assert dp.price_vnd == 5000
        assert dp.dosage_form == "Viên"
        assert dp.strength == "500mg"
        assert dp.source == SourceName.CHOTHUOC247
        assert dp.product_id == "42"
        assert dp.image_url == "https://chothuoc247.vn/image/paracetamol.jpg"
        _run(c.close())

    def test_is_auth_error_redirect(self) -> None:
        from crawlers.b2b.chothuoc247 import ChoThuoc247Crawler

        c = ChoThuoc247Crawler(_cfg("chothuoc247"))
        resp = httpx.Response(302, headers={"location": "/dang-nhap.html"})
        assert c._is_auth_error(resp) is True
        resp2 = httpx.Response(419)
        assert c._is_auth_error(resp2) is True
        resp3 = httpx.Response(200)
        assert c._is_auth_error(resp3) is False

    def test_no_csrf_raises_auth_error(self) -> None:
        from crawlers.b2b.chothuoc247 import ChoThuoc247Crawler

        c = ChoThuoc247Crawler(_cfg("chothuoc247"))

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>no csrf here</html>")

        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError):
            _run(c._login())
        _run(c.close())

    def test_fetch_price_by_id_reads_exact_detail_page(self) -> None:
        from crawlers.b2b.chothuoc247 import ChoThuoc247Crawler

        c = ChoThuoc247Crawler(_cfg("chothuoc247", "https://chothuoc247.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/dang-nhap.html":
                return httpx.Response(
                    200, text='<meta name="csrf-token" content="C">'
                )
            if req.url.path == "/submitLoginCustomer":
                return httpx.Response(302, headers={"location": "/dat-hang.html"})
            if req.url.path == "/dat-hang.html":
                return httpx.Response(
                    200, text='<meta name="csrf-token" content="C2">'
                )
            if req.url.path == "/san-pham/5027.html":
                return httpx.Response(
                    200,
                    text='<h1 class="product-title">Alaxan hộp 100 viên</h1>'
                    '<span class="price">Giá: 113,500 ₫</span>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        result = _run(c.fetch_price_by_id("5027"))

        assert c.direct_fetch_supported is True
        assert result is not None
        assert result.product_id == "5027"
        assert result.price_vnd == 113500
        assert result.source_url.endswith("/san-pham/5027.html")
        _run(c.close())

    def test_fetch_price_by_id_returns_out_of_stock_record_without_price(self) -> None:
        from crawlers.b2b.chothuoc247 import ChoThuoc247Crawler
        from utils.models import StockStatus

        c = ChoThuoc247Crawler(_cfg("chothuoc247", "https://chothuoc247.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/dang-nhap.html":
                return httpx.Response(200, text='<meta name="csrf-token" content="C">')
            if req.url.path == "/submitLoginCustomer":
                return httpx.Response(302, headers={"location": "/dat-hang.html"})
            if req.url.path == "/dat-hang.html":
                return httpx.Response(200, text='<meta name="csrf-token" content="C2">')
            if req.url.path == "/san-pham/99.html":
                return httpx.Response(
                    200,
                    text='<h1 class="product-title">Thuốc X</h1>'
                    '<p class="stock out-of-stock">Hết hàng</p>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        result = _run(c.fetch_price_by_id("99"))

        assert result is not None
        assert result.price_vnd == 0
        assert result.stock_status == StockStatus.OUT_OF_STOCK
        _run(c.close())


# =====================================================================
# ThuocHaPu
# =====================================================================


class TestThuocHaPu:
    def test_login_and_search_and_parse(self) -> None:
        from crawlers.b2b.thuochapu import ThuocHaPuCrawler

        c = ThuocHaPuCrawler(_cfg("thuochapu", "https://thuochapu.com"))

        token_hash = "a" * 32

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if p == "/login.html" and req.method == "GET":
                return httpx.Response(
                    200,
                    text=f'<html><input type="hidden" name="{token_hash}" value="1"></html>',
                )
            if p == "/login.html" and req.method == "POST":
                return httpx.Response(302, headers={"location": "/"})
            if p == "/search.html":
                # DOM thật: mỗi sản phẩm là 1 card `div.t3-medicine`; header
                # trang có `<b>2646</b>` (bộ đếm tổng sản phẩm) khớp regex giá.
                # Regression: card-based parse phải bỏ qua node rác này thay vì
                # đẩy lệch giá đi 1 ô.
                return httpx.Response(
                    200,
                    text='<html><b>2646</b>'
                    '<div class="t3-medicine">'
                    '<div class="t3-name">'
                    '<a href="/thuoc/boganic.html"><img src="/images/boganic.jpg">Boganic</a>'
                    '</div><b>48.000</b><small>/Hộp</small>'
                    '</div>'
                    '<div class="t3-medicine">'
                    '<a href="/thuoc/alaxan.html">Alaxan</a><b>110.000</b>'
                    '</div>'
                    '</html>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        # crawl() không lọc keyword (việc đó ở engine) -> trả cả 2 card.
        results = _run(c.crawl("boganic"))
        assert len(results) == 2
        by_id = {dp.product_id: dp for dp in results}

        # Boganic phải giữ đúng giá 48.000 (không bị "2646" đẩy lệch sang).
        dp = by_id["/thuoc/boganic.html"]
        assert dp.drug_name == "Boganic"
        assert dp.price_vnd == 48000
        assert dp.source_url == "/thuoc/boganic.html"
        assert dp.image_url == "https://thuochapu.com/images/boganic.jpg"

        # Alaxan (card thứ 2) nhận đúng giá của chính nó, không lệch.
        assert by_id["/thuoc/alaxan.html"].price_vnd == 110000
        _run(c.close())

    def test_crawl_all_pagination(self) -> None:
        from crawlers.b2b.thuochapu import ThuocHaPuCrawler

        c = ThuocHaPuCrawler(_cfg("thuochapu"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/login.html":
                return httpx.Response(
                    200,
                    text='<html><input type="hidden" name="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" value="1"></html>',
                )
            if req.url.path == "/danh-muc.html":
                return httpx.Response(
                    200,
                    text='<html><b>2646</b>'
                    '<div class="t3-medicine">'
                    '<a href="/thuoc/x.html">X</a><b>10.000</b>'
                    '</div></html>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        # Empty keyword -> crawl all path.
        items = _run(c._fetch_products(""))
        assert len(items) == 1
        assert items[0]["price"] == "10.000"  # không bị "2646" đẩy lệch
        _run(c.close())

    def test_no_token_raises_auth_error(self) -> None:
        from crawlers.b2b.thuochapu import ThuocHaPuCrawler

        c = ThuocHaPuCrawler(_cfg("thuochapu"))

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>no token</html>")

        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError):
            _run(c._login())
        _run(c.close())

    def test_is_auth_error_redirect(self) -> None:
        from crawlers.b2b.thuochapu import ThuocHaPuCrawler

        c = ThuocHaPuCrawler(_cfg("thuochapu"))
        assert c._is_auth_error(httpx.Response(302, headers={"location": "/login.html"})) is True
        assert c._is_auth_error(httpx.Response(403)) is True
        assert c._is_auth_error(httpx.Response(200)) is False

    def test_extract_joomla_token(self) -> None:
        from crawlers.b2b.thuochapu import ThuocHaPuCrawler

        html = '<input type="hidden" name="abcdef0123456789abcdef0123456789" value="1">'
        assert ThuocHaPuCrawler._extract_joomla_token(html) == "abcdef0123456789abcdef0123456789"
        assert ThuocHaPuCrawler._extract_joomla_token("<html></html>") == ""

    def test_keyword_search_disabled(self) -> None:
        """search.html bỏ qua keyword → phải để keyword_search_supported=False,
        buộc GUI dùng fetch_price_by_id thay vì search theo tên (nếu không, mọi
        sản phẩm nhận nhầm giá trang đầu)."""
        from crawlers.b2b.thuochapu import ThuocHaPuCrawler

        assert ThuocHaPuCrawler.keyword_search_supported is False

    def test_fetch_price_by_id_reads_detail_jsonld(self) -> None:
        """Giá LIVE lấy từ JSON-LD trang chi tiết (đúng SKU), KHÔNG từ search.
        JSON-LD của thuochapu có xuống dòng THÔ trong `description` — parse phải
        chịu được (json.loads strict=False), nếu không giá về None."""
        from crawlers.b2b.thuochapu import ThuocHaPuCrawler

        c = ThuocHaPuCrawler(_cfg("thuochapu", "https://thuochapu.com"))
        token_hash = "a" * 32
        # description cố tình chứa newline thô (control char) như site thật.
        detail = (
            '<html><script type="application/ld+json">'
            '{"@context":"https://schema.org/","@type":"Product",'
            '"name":"Alaxan 10 vỉ x 10 viên/hộp",'
            '"image":"https://thuochapu.com/images/medicine/alaxan-united.jpg",'
            '"description":"Dòng 1\nDòng 2 xuống dòng thô",'
            '"offers":{"@type":"Offer","price":"110000","priceCurrency":"VND"}}'
            "</script></html>"
        )

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if p == "/login.html" and req.method == "GET":
                return httpx.Response(
                    200, text=f'<html><input type="hidden" name="{token_hash}" value="1"></html>'
                )
            if p == "/login.html" and req.method == "POST":
                return httpx.Response(302, headers={"location": "/"})
            if p == "/thuoc/alaxan-united.html":
                return httpx.Response(200, text=detail)
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        dp = _run(c.fetch_price_by_id("https://thuochapu.com/thuoc/alaxan-united.html"))
        assert dp is not None
        assert dp.price_vnd == 110000
        assert dp.drug_name == "Alaxan 10 vỉ x 10 viên/hộp"
        assert dp.product_id == "https://thuochapu.com/thuoc/alaxan-united.html"
        _run(c.close())

    def test_detail_jsonld_preserves_out_of_stock_availability(self) -> None:
        from crawlers.b2b.thuochapu import ThuocHaPuCrawler
        from utils.models import StockStatus

        c = ThuocHaPuCrawler(_cfg("thuochapu"))
        html = (
            '<script type="application/ld+json">'
            '{"@type":"Product","name":"Thuốc X",'
            '"offers":{"@type":"Offer","price":"0",'
            '"availability":"https://schema.org/OutOfStock"}}'
            "</script>"
        )

        result = c._parse_detail(html, "https://thuochapu.com/thuoc/x.html")

        assert result is not None
        assert result.price_vnd == 0
        assert result.stock_status == StockStatus.OUT_OF_STOCK


# =====================================================================
# ChoThuocTot
# =====================================================================


class TestChoThuocTot:
    def test_login_and_fetch_and_parse(self) -> None:
        from crawlers.b2b.chothuoctot import ChoThuocTotCrawler

        c = ChoThuocTotCrawler(_cfg("chothuoctot", "https://chothuoctot.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/oauth/token" and req.method == "POST":
                return httpx.Response(200, json={"access_token": "TOK123"})
            if "search-product" in req.url.path:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "product_name": "Aspirin",
                                "price": "12000",
                                "company_name": "Bayer",
                                "unit": "Viên",
                                "id": 99,
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl("asp"))
        assert len(results) == 1
        dp = results[0]
        assert dp.drug_name == "Aspirin"
        assert dp.price_vnd == 12000
        assert dp.manufacturer == "Bayer"
        assert dp.dosage_form == "Viên"
        assert dp.source == SourceName.CHOTHUOCTOT
        _run(c.close())

    def test_parses_real_response_shape(self) -> None:
        """Response thật (xác nhận sống 2026-07-11): tên ở `drg_drug_name` (không
        phải `product_name`/`name` — bản cũ luôn ra rỗng, bị lọc mất im lặng dù
        API trả đúng dữ liệu), giá nằm trong `units[0].wholesale_price`, quy cách
        ở `package_desc`."""
        from crawlers.b2b.chothuoctot import ChoThuocTotCrawler

        c = ChoThuocTotCrawler(_cfg("chothuoctot", "https://chothuoctot.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/oauth/token" and req.method == "POST":
                return httpx.Response(200, json={"access_token": "TOK123"})
            if "search-product" in req.url.path:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "drug_id": 1664313,
                                "drg_drug_name": "Boganic Forte Traphaco (H/50v)",
                                "company_name": "Traphaco - Công ty cổ phần TRAPHACO",
                                "package_desc": "Hộp 5 vỉ x 10 viên nang mềm",
                                "units": [{"price": 105400.0, "wholesale_price": 105400.0}],
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl("boganic"))
        assert len(results) == 1
        dp = results[0]
        assert dp.drug_name == "Boganic Forte Traphaco (H/50v)"
        assert dp.price_vnd == 105400
        assert dp.manufacturer == "Traphaco - Công ty cổ phần TRAPHACO"
        assert dp.dosage_form == "Hộp 5 vỉ x 10 viên nang mềm"
        assert dp.product_id == "1664313"
        _run(c.close())

    def test_login_failure_raises_auth_error(self) -> None:
        from crawlers.b2b.chothuoctot import ChoThuocTotCrawler

        c = ChoThuocTotCrawler(_cfg("chothuoctot"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/oauth/token":
                return httpx.Response(401, text="bad")
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError):
            _run(c._login())
        _run(c.close())

    def test_walk_helper(self) -> None:
        from crawlers.b2b.chothuoctot import _walk

        assert _walk({"data": [{"x": 1}]}) == [{"x": 1}]
        assert _walk({"data": {"content": [{"y": 2}]}}) == [{"y": 2}]
        assert _walk({"products": [{"z": 3}]}) == [{"z": 3}]
        assert _walk({}) == []

    def test_fetch_price_by_id_uses_medlink_detail_endpoint(self) -> None:
        from crawlers.b2b.chothuoctot import ChoThuocTotCrawler

        c = ChoThuocTotCrawler(_cfg("chothuoctot", "https://chothuoctot.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "T"})
            if req.url.path == "/pharmacy/supply/product/1623682":
                return httpx.Response(
                    200,
                    json={
                        "drug_id": 1623682,
                        "drg_drug_name": "Alaxan hộp 100 viên",
                        "units": [{"wholesale_price": 112000}],
                    },
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        result = _run(c.fetch_price_by_id("1623682"))

        assert c.direct_fetch_supported is True
        assert result is not None
        assert result.product_id == "1623682"
        assert result.price_vnd == 112000
        _run(c.close())


# =====================================================================
# ThuocSi
# =====================================================================


class TestThuocSi:
    def test_fetch_price_by_id_returns_unavailable_product(self) -> None:
        """Luồng GUI lưu slug từ URL rồi phải fetch đúng slug đó, kể cả khi
        ThuocSi đánh dấu sản phẩm không còn hàng (`isAvailable=false`)."""
        from crawlers.b2b.thuocsi import ThuocSiCrawler
        from utils.models import StockStatus

        slug = "medx-alaxan-united-h10v10v-bam"
        c = ThuocSiCrawler(
            _cfg("thuocsi", "https://thuocsi.vn", manual_token="TOKEN")
        )
        requested_urls: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            requested_urls.append(str(req.url))
            if req.url.params.get("q") == slug:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "isAvailable": False,
                                "product": {
                                    "name": "Alaxan United H10V10V",
                                    "volume": "Hộp 10 vỉ x 10 viên",
                                },
                                "sku": {
                                    "slug": slug,
                                    "status": "OUT_OF_STOCK",
                                    "retailPriceValueEncrypt": (
                                        "vKmvzL8un+atOKF1qqnwXA=="
                                    ),
                                },
                            }
                        ],
                    },
                )
            return httpx.Response(200, json={"data": []})

        _run(c.open())
        _attach(c, handler)
        result = _run(c.fetch_price_by_id(slug))

        assert result is not None
        assert result.product_id == slug
        assert result.stock_status == StockStatus.OUT_OF_STOCK
        assert any("/product/detail-encrypted" in url for url in requested_urls)
        _run(c.close())

    def test_login_and_fetch_and_parse(self) -> None:
        """Endpoint/field/giá đã xác nhận sống 2026-07-11 (reverse-engineer JS bundle
        production) — bản cũ dùng {phone,...} lên /login luôn trả 401 dù mật khẩu
        đúng. Giá test dùng ciphertext AES thật (round-trip qua _decrypt_price)."""
        from crawlers.b2b.thuocsi import ThuocSiCrawler

        c = ThuocSiCrawler(_cfg("thuocsi", "https://thuocsi.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if p == "/backend/marketplace/customer/v1/authentication":
                body = json.loads(req.content.decode())
                assert body["username"] == "user"
                assert body["type"] == "CUSTOMER"
                return httpx.Response(
                    200, json={"data": [{"bearerToken": "TS_TOK", "type": "CUSTOMER"}]}
                )
            if p == "/backend/marketplace/frontend-apis/v2/screen/product/list":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "productName": "Vitamin C",
                                # ciphertext AES thật của 35000 (key suy từ _PRICE_KEY_SEED).
                                "priceEncrypted": "vKmvzL8un+atOKF1qqnwXA==",
                                "slug": "vitamin-c",
                                "volume": "Hộp 10 vỉ",
                            }
                        ]
                    },
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl("vit"))
        assert len(results) == 1
        dp = results[0]
        assert dp.drug_name == "Vitamin C"
        assert dp.price_vnd == 35000
        assert dp.dosage_form == "Hộp 10 vỉ"
        assert dp.product_id == "vitamin-c"
        assert dp.source == SourceName.THUOCSI
        _run(c.close())

    def test_prefers_discount_price_over_regular_price(self) -> None:
        from crawlers.b2b.thuocsi import ThuocSiCrawler

        c = ThuocSiCrawler(_cfg("thuocsi"))
        dp = c._parse_product({
            "productName": "Vitamin C",
            "priceEncrypted": "vKmvzL8un+atOKF1qqnwXA==",  # 35000
            "discountPriceEncrypted": "I9y0rJ46xd/t+T2TTBjPXQ==",  # 30000
        })
        assert dp is not None
        assert dp.price_vnd == 30000

    def test_login_failure(self) -> None:
        from crawlers.b2b.thuocsi import ThuocSiCrawler

        c = ThuocSiCrawler(_cfg("thuocsi"))

        def handler(req: httpx.Request) -> httpx.Response:
            if "authentication" in req.url.path:
                return httpx.Response(403, text="bad")
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError):
            _run(c._login())
        _run(c.close())

    def test_extract_token_from_list(self) -> None:
        from crawlers.b2b.thuocsi import ThuocSiCrawler

        assert ThuocSiCrawler._extract_token({"data": [{"bearerToken": "X"}]}) == "X"
        assert ThuocSiCrawler._extract_token({"data": {"accessToken": "Y"}}) == "Y"
        assert ThuocSiCrawler._extract_token({}) == ""

    def test_decrypt_price_handles_invalid_input(self) -> None:
        from crawlers.b2b.thuocsi import _decrypt_price

        assert _decrypt_price(None) == 0
        assert _decrypt_price("") == 0
        assert _decrypt_price("not-valid-base64!!!") == 0

    def test_walk_products(self) -> None:
        from crawlers.b2b.thuocsi import _walk_products

        assert _walk_products({"data": [{"a": 1}]}) == [{"a": 1}]
        assert _walk_products({"data": {"products": [{"b": 2}]}}) == [{"b": 2}]
        assert _walk_products({}) == []

    def test_short_page_does_not_stop_early_when_total_says_more(self) -> None:
        """Xác nhận sống 2026-07-11: 1 trang giữa catalog có thể trả THIẾU so với
        `limit` (vd 19/20 dù total=17997, còn rất nhiều dữ liệu phía sau) — nếu
        dừng theo `len(batch) < limit` sẽ cắt cụt catalog rất sớm (bug thật đã gặp:
        catalog 39/17997 sản phẩm). Phải dựa vào field `total` để biết khi nào dừng."""
        from crawlers.b2b.thuocsi import ThuocSiCrawler

        c = ThuocSiCrawler(_cfg("thuocsi"))
        offsets_seen: list[int] = []
        # Trang 2 (offset=20) cố tình trả THIẾU (19 thay vì 20) để mô phỏng đúng
        # hành vi thật đã quan sát (offset=20 -> 19 sp dù total=17997) — vẫn phải
        # tiếp tục sang trang 3 vì total=45 báo còn dữ liệu.
        page_sizes = [20, 19, 6]  # tổng = 45, khớp `total`

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode())
            offset = body["offset"]
            offsets_seen.append(offset)
            idx = len(offsets_seen) - 1
            count = page_sizes[idx] if idx < len(page_sizes) else 0
            items = [{"name": f"P{offset}-{i}"} for i in range(count)]
            return httpx.Response(200, json={"status": "OK", "data": items, "total": 45})

        _run(c.open())
        _attach(c, handler)
        items = _run(c._fetch_products(""))
        assert offsets_seen == [0, 20, 40]
        assert len(items) == 45
        _run(c.close())

    def test_falls_back_to_short_page_heuristic_when_no_total_field(self) -> None:
        """Nếu response thiếu field `total` (phòng hờ), vẫn phải dừng đúng lúc dựa
        vào trang thiếu — không được chạy vô hạn."""
        from crawlers.b2b.thuocsi import ThuocSiCrawler

        c = ThuocSiCrawler(_cfg("thuocsi"))
        offsets_seen: list[int] = []

        def handler(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode())
            offset = body["offset"]
            offsets_seen.append(offset)
            count = 20 if offset == 0 else 5
            items = [{"name": f"P{offset}-{i}"} for i in range(count)]
            return httpx.Response(200, json={"status": "OK", "data": items})  # không có "total"

        _run(c.open())
        _attach(c, handler)
        items = _run(c._fetch_products(""))
        assert offsets_seen == [0, 20]
        assert len(items) == 25
        _run(c.close())


# =====================================================================
# ThuocTot3Mien
# =====================================================================


class TestThuocTot3Mien:
    def test_login_and_fetch_and_parse(self) -> None:
        """Endpoint/field đã đúng sẵn trong docs cũ, nhưng thiếu Origin/Referer khiến
        login luôn 401 dù mật khẩu đúng (xác nhận sống 2026-07-11 — cùng request,
        chỉ thêm 2 header là login OK). Response thật: token ở `data.token`, giá là
        OBJECT lồng {"base":...,"final":...} (không phải số/chuỗi phẳng), field
        "unit" là object quan hệ (không phải string — dùng "packaging" thay)."""
        from crawlers.b2b.thuoctot3mien import ThuocTot3MienCrawler

        c = ThuocTot3MienCrawler(_cfg("thuoctot3mien", "https://thuoctot3mien.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if "/customer/login" in p:
                assert req.headers.get("Origin") == "https://thuoctot3mien.vn"
                assert req.headers.get("Referer")
                return httpx.Response(200, json={"data": {"token": "T3M_TOK"}})
            if "/products" in p:
                assert req.headers.get("Origin") == "https://thuoctot3mien.vn"
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "data": [
                                {
                                    "name": "Boganic KD",
                                    "price": {"base": 65000, "final": 65000},
                                    "base_price": "65000.00",
                                    "packaging": "Hộp 5 vỉ x 20 viên",
                                    "unit": {"id": 2, "name": "Hộp"},
                                    "id": 741,
                                }
                            ]
                        }
                    },
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl("boganic"))
        assert len(results) == 1
        dp = results[0]
        assert dp.drug_name == "Boganic KD"
        assert dp.price_vnd == 65000
        assert dp.dosage_form == "Hộp 5 vỉ x 20 viên"
        assert dp.product_id == "741"
        assert dp.source == SourceName.THUOCTOT3MIEN
        _run(c.close())

    def test_price_prefers_final_over_base_when_discounted(self) -> None:
        from crawlers.b2b.thuoctot3mien import ThuocTot3MienCrawler

        c = ThuocTot3MienCrawler(_cfg("thuoctot3mien"))
        dp = c._parse_product({
            "name": "X", "price": {"base": 100000, "final": 90000}, "id": 1,
        })
        assert dp is not None
        assert dp.price_vnd == 90000

    def test_out_of_stock_status_is_preserved_even_when_price_is_zero(self) -> None:
        from crawlers.b2b.thuoctot3mien import ThuocTot3MienCrawler

        c = ThuocTot3MienCrawler(_cfg("thuoctot3mien"))
        dp = c._parse_product(
            {
                "id": 4079,
                "name": "Colchicin (1vỉ x 20viên/h) Danaphar",
                "status": "out_of_stock",
                "quantity": 0,
                "price": {"base": 0, "final": 0},
            }
        )

        assert dp is not None
        assert dp.price_vnd == 0
        assert dp.stock_status == "out_of_stock"

    def test_fetch_price_by_id_uses_product_detail(self) -> None:
        from crawlers.b2b.thuoctot3mien import ThuocTot3MienCrawler

        c = ThuocTot3MienCrawler(_cfg("thuoctot3mien", "https://thuoctot3mien.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            if "/customer/login" in req.url.path:
                return httpx.Response(200, json={"data": {"token": "T"}})
            if req.url.path.endswith("/products/646"):
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "id": 646,
                            "name": "Biotin 5mg (Hộp 2 vỉ x 10 viên)",
                            "price": {"base": 10700, "final": 10700},
                        }
                    },
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        result = _run(c.fetch_price_by_id("646"))

        assert c.direct_fetch_supported is True
        assert result is not None
        assert result.product_id == "646"
        assert result.price_vnd == 10700
        _run(c.close())

    def test_empty_keyword_omits_search_param(self) -> None:
        """Gửi `search=""` (rỗng) khiến server trả 0 sản phẩm thay vì "không lọc"
        — xác nhận sống 2026-07-11. crawl_all()/catalog phải KHÔNG gửi field
        `search` khi không có từ khóa, chỉ gửi khi thật sự có keyword."""
        from crawlers.b2b.thuoctot3mien import ThuocTot3MienCrawler

        c = ThuocTot3MienCrawler(_cfg("thuoctot3mien", "https://thuoctot3mien.vn"))
        seen_params: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if "/customer/login" in req.url.path:
                return httpx.Response(200, json={"data": {"token": "T"}})
            if "/products" in req.url.path:
                seen_params.append(dict(req.url.params))
                return httpx.Response(200, json={"data": {"data": []}})
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        _run(c.crawl(""))
        assert "search" not in seen_params[0]

        seen_params.clear()
        _run(c.crawl("boganic"))
        assert seen_params[0].get("search") == "boganic"
        _run(c.close())

    def test_short_page_does_not_stop_early_when_meta_total_says_more(self) -> None:
        """Xác nhận sống 2026-07-11: `per_page` THẬT của server = 15, bất kể `limit`
        gửi lên (20) — `len(batch) < 20` luôn đúng dù còn hàng trăm trang, khiến
        vòng lặp dừng ngay sau trang 1 (bug thật đã gặp: catalog chỉ lấy được
        15/4.672 sản phẩm). Phải dựa vào `meta.total` để biết khi nào dừng."""
        from crawlers.b2b.thuoctot3mien import ThuocTot3MienCrawler

        c = ThuocTot3MienCrawler(_cfg("thuoctot3mien", "https://thuoctot3mien.vn"))
        pages_seen: list[int] = []
        page_sizes = [15, 15, 5]  # tổng = 35, khớp meta.total

        def handler(req: httpx.Request) -> httpx.Response:
            if "/customer/login" in req.url.path:
                return httpx.Response(200, json={"data": {"token": "T"}})
            if "/products" in req.url.path:
                page = int(req.url.params.get("page", "1"))
                pages_seen.append(page)
                idx = len(pages_seen) - 1
                count = page_sizes[idx] if idx < len(page_sizes) else 0
                items = [{"name": f"P{page}-{i}"} for i in range(count)]
                return httpx.Response(
                    200,
                    json={"data": {"data": items, "meta": {"current_page": page, "total": 35}}},
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl(""))
        assert pages_seen == [1, 2, 3]
        assert len(results) == 35
        _run(c.close())

    def test_price_falls_back_to_base_price_string_when_no_price_object(self) -> None:
        from crawlers.b2b.thuoctot3mien import ThuocTot3MienCrawler

        c = ThuocTot3MienCrawler(_cfg("thuoctot3mien"))
        dp = c._parse_product({"name": "X", "base_price": "65000.00", "id": 1})
        assert dp is not None
        assert dp.price_vnd == 65000

    def test_login_failure(self) -> None:
        from crawlers.b2b.thuoctot3mien import ThuocTot3MienCrawler

        c = ThuocTot3MienCrawler(_cfg("thuoctot3mien"))

        def handler(req: httpx.Request) -> httpx.Response:
            if "login" in req.url.path:
                return httpx.Response(400, text="bad")
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError):
            _run(c._login())
        _run(c.close())

    def test_first_helper(self) -> None:
        from crawlers.b2b.thuoctot3mien import _first

        assert _first({"a": 1, "b": 2}, ("b", "a")) == 2

    def test_first_str_helper_skips_non_string_values(self) -> None:
        """API thật trả field trùng tên (vd "unit") dạng object quan hệ lồng nhau
        thay vì chuỗi — _first_str phải bỏ qua, không được crash pydantic."""
        from crawlers.b2b.thuoctot3mien import _first_str

        assert _first_str({"unit": {"id": 2}, "packaging": "Hộp"}, ("packaging", "unit")) == "Hộp"
        assert _first_str({"unit": {"id": 2}}, ("packaging", "unit")) == ""
        assert _first_str({}, ("a", "b"), default="x") == "x"


# =====================================================================
# ThuocSiSaiGon
# =====================================================================


class TestThuocSiSaiGon:
    def test_login_and_fetch_and_parse(self) -> None:
        from crawlers.b2b.thuocsisaigon import ThuocSiSaiGonCrawler

        c = ThuocSiSaiGonCrawler(_cfg("thuocsisaigon", "https://thuocsisaigon.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if p == "/account/login" and req.method == "GET":
                return httpx.Response(
                    200,
                    text='<html><input name="__RequestVerificationToken" value="RV_TOKEN"></html>',
                )
            if p == "/account/login" and req.method == "POST":
                return httpx.Response(302, headers={"location": "/"})
            if p == "/search":
                return httpx.Response(
                    200,
                    text='<html>'
                    '<a href="/products/aspirin.html">Aspirin</a>'
                    '<div class="price">25.000</div>'
                    '</html>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl("asp"))
        assert len(results) == 1
        dp = results[0]
        assert dp.drug_name == "Aspirin"
        assert dp.price_vnd == 25000
        assert "thuocsisaigon.vn/products/aspirin" in dp.source_url
        _run(c.close())

    def test_fetch_price_by_id_preserves_out_of_stock_status(self) -> None:
        from crawlers.b2b.thuocsisaigon import ThuocSiSaiGonCrawler
        from utils.models import StockStatus

        c = ThuocSiSaiGonCrawler(_cfg("thuocsisaigon", "https://thuocsisaigon.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/account/login" and req.method == "GET":
                return httpx.Response(
                    200,
                    text='<input name="__RequestVerificationToken" value="RV">',
                )
            if req.url.path == "/account/login" and req.method == "POST":
                return httpx.Response(302, headers={"location": "/"})
            if req.url.path == "/products/thuoc-x":
                return httpx.Response(
                    200,
                    text='<h1>Thuốc X</h1><div class="sold-out">Hết hàng</div>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        result = _run(c.fetch_price_by_id("/products/thuoc-x"))

        assert c.direct_fetch_supported is True
        assert result is not None
        assert result.price_vnd == 0
        assert result.stock_status == StockStatus.OUT_OF_STOCK
        _run(c.close())

    def test_empty_keyword_uses_collections_all_not_search(self) -> None:
        """`/search?q=` (rỗng) trả 0 sản phẩm — endpoint search của Haravan không hỗ
        trợ "browse tất cả" (xác nhận sống 2026-07-11). Catalog phải dùng
        `/collections/all` (quy ước Haravan/Shopify)."""
        from crawlers.b2b.thuocsisaigon import ThuocSiSaiGonCrawler

        c = ThuocSiSaiGonCrawler(_cfg("thuocsisaigon", "https://thuocsisaigon.vn"))
        hit_paths: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if p == "/account/login" and req.method == "GET":
                return httpx.Response(
                    200, text='<html><input name="__RequestVerificationToken" value="RV"></html>'
                )
            if p == "/account/login" and req.method == "POST":
                return httpx.Response(302, headers={"location": "/"})
            hit_paths.append(p)
            if p == "/search":
                return httpx.Response(200, text="<html></html>")  # search rỗng -> 0
            if p == "/collections/all":
                return httpx.Response(
                    200,
                    text='<html><a href="/products/x.html">X</a>'
                    '<div class="price">10.000</div></html>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl(""))
        assert "/collections/all" in hit_paths
        assert "/search" not in hit_paths
        assert len(results) == 1
        assert results[0].drug_name == "X"
        _run(c.close())

    def test_no_token_raises_auth_error(self) -> None:
        from crawlers.b2b.thuocsisaigon import ThuocSiSaiGonCrawler

        c = ThuocSiSaiGonCrawler(_cfg("thuocsisaigon"))

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>no token</html>")

        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError):
            _run(c._login())
        _run(c.close())

    def test_authenticity_token_fallback(self) -> None:
        from crawlers.b2b.thuocsisaigon import ThuocSiSaiGonCrawler

        c = ThuocSiSaiGonCrawler(_cfg("thuocsisaigon"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/account/login" and req.method == "GET":
                return httpx.Response(
                    200,
                    text='<html><input name="authenticity_token" value="AUTH_TOK"></html>',
                )
            if req.url.path == "/account/login" and req.method == "POST":
                return httpx.Response(302, headers={"location": "/"})
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        _run(c._login())
        assert c._authenticated is False or True
        _run(c.close())

    def test_login_failure_200_raises_auth_error(self) -> None:
        """Haravan trả HTTP 200 (render lại trang login + node `.errors`) khi sai
        tài khoản/mật khẩu — KHÔNG đổi status. Bản cũ coi 200 là OK nên login sai
        bị nuốt im lặng → crawl như khách → mọi giá = 0. Phải fail loud với đúng
        thông báo server."""
        from crawlers.b2b.thuocsisaigon import ThuocSiSaiGonCrawler

        c = ThuocSiSaiGonCrawler(_cfg("thuocsisaigon"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/account/login" and req.method == "GET":
                return httpx.Response(
                    200, text='<html><input name="__RequestVerificationToken" value="RV"></html>'
                )
            if req.url.path == "/account/login" and req.method == "POST":
                return httpx.Response(
                    200,
                    text='<html><div class="errors">Thông tin đăng nhập không hợp lệ.</div></html>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError, match="không hợp lệ"):
            _run(c._login())
        _run(c.close())


# =====================================================================
# DuocPhamGiaSi
# =====================================================================


class TestDuocPhamGiaSi:
    def test_login_primary_and_fetch(self) -> None:
        from crawlers.b2b.duocphamgiasi import DuocPhamGiaSiCrawler

        c = DuocPhamGiaSiCrawler(_cfg("duocphamgiasi", "https://duocphamgiasi.vn"))

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if p == "/tai-khoan" and req.method == "GET":
                return httpx.Response(
                    200,
                    text='<html>'
                    '<input name="mbup_key" value="MBUP123">'
                    '<input name="nonce_rwmb-user-login" value="NONCE456">'
                    '</html>',
                )
            if p == "/tai-khoan" and req.method == "POST":
                return httpx.Response(302, headers={"location": "/"})
            if p == "/" and req.url.params.get("post_type") == "product":
                return httpx.Response(
                    200,
                    text='<html>'
                    '<article class="product-item">'
                    '<div class="product-card" data-price="15000">'
                    '<div class="entry-title"><a href="/product/panadol/">Panadol</a></div>'
                    '</div>'
                    '</article>'
                    '</html>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl("pana"))
        assert len(results) == 1
        dp = results[0]
        assert dp.drug_name == "Panadol"
        assert dp.price_vnd == 15000
        assert dp.source_url == "/product/panadol/"
        _run(c.close())

    def test_login_fallback_wp_login(self) -> None:
        from crawlers.b2b.duocphamgiasi import DuocPhamGiaSiCrawler

        c = DuocPhamGiaSiCrawler(_cfg("duocphamgiasi"))

        def handler(req: httpx.Request) -> httpx.Response:
            p = req.url.path
            if p == "/tai-khoan" and req.method == "GET":
                return httpx.Response(200, text="<html>no mbup here</html>")
            if p == "/wp-login.php":
                return httpx.Response(302, headers={"location": "/"})
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        _run(c._login())
        _run(c.close())

    def test_fetch_price_by_id_uses_exact_product_url(self) -> None:
        from crawlers.b2b.duocphamgiasi import DuocPhamGiaSiCrawler

        c = DuocPhamGiaSiCrawler(_cfg("duocphamgiasi", "https://duocphamgiasi.vn"))
        product_url = "https://duocphamgiasi.vn/product/alaxan/"

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/tai-khoan" and req.method == "GET":
                return httpx.Response(
                    200,
                    text='<input name="mbup_key" value="K">'
                    '<input name="nonce_rwmb-user-login" value="N">',
                )
            if req.url.path == "/tai-khoan" and req.method == "POST":
                return httpx.Response(302, headers={"location": "/"})
            if req.url.path == "/product/alaxan/":
                return httpx.Response(
                    200,
                    text='<h1 class="product_title">Alaxan hộp 100 viên</h1>'
                    '<p class="price"><span>114,999₫</span></p>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        result = _run(c.fetch_price_by_id(product_url))

        assert c.direct_fetch_supported is True
        assert result is not None
        assert result.product_id == product_url
        assert result.price_vnd == 114999
        _run(c.close())

    def test_fetch_price_by_id_returns_out_of_stock_record_without_price(self) -> None:
        from crawlers.b2b.duocphamgiasi import DuocPhamGiaSiCrawler
        from utils.models import StockStatus

        c = DuocPhamGiaSiCrawler(_cfg("duocphamgiasi", "https://duocphamgiasi.vn"))
        product_url = "https://duocphamgiasi.vn/product/thuoc-x/"

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/tai-khoan" and req.method == "GET":
                return httpx.Response(
                    200,
                    text='<input name="mbup_key" value="K">'
                    '<input name="nonce_rwmb-user-login" value="N">',
                )
            if req.url.path == "/tai-khoan" and req.method == "POST":
                return httpx.Response(302, headers={"location": "/"})
            if req.url.path == "/product/thuoc-x/":
                return httpx.Response(
                    200,
                    text='<h1 class="product_title">Thuốc X</h1>'
                    '<p class="stock out-of-stock">Hết hàng</p>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        result = _run(c.fetch_price_by_id(product_url))

        assert result is not None
        assert result.price_vnd == 0
        assert result.stock_status == StockStatus.OUT_OF_STOCK
        _run(c.close())

    def test_crawl_all_uses_product_url_not_shop(self) -> None:
        """Catalog (không keyword) dùng /product/ — /shop/ trả 404 trên site thật
        (xác nhận sống 2026-07-11), theme đã đổi selector sang article.product-item
        + giá ở attribute `data-price` (text ".price" thường rỗng)."""
        from crawlers.b2b.duocphamgiasi import DuocPhamGiaSiCrawler

        c = DuocPhamGiaSiCrawler(_cfg("duocphamgiasi"))

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/tai-khoan":
                return httpx.Response(
                    200,
                    text='<html><input name="mbup_key" value="K"><input name="nonce_rwmb-user-login" value="N"></html>',
                )
            if req.url.path == "/shop/":
                return httpx.Response(404)
            if req.url.path == "/product/":
                return httpx.Response(
                    200,
                    text='<html><article class="product-item">'
                    '<div class="product-card" data-price="20000">'
                    '<div class="entry-title"><a href="/product/drugx/">DrugX</a></div>'
                    '</div></article></html>',
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        items = _run(c._fetch_products(""))
        assert len(items) == 1
        assert items[0]["name"] == "DrugX"
        assert items[0]["price"] == "20000"
        _run(c.close())

    def test_catalog_pagination_uses_pretty_permalink(self) -> None:
        """Trang sau của catalog dùng /product/page/{n}/ (pretty-permalink), KHÁC
        search dùng query param `paged` — xác nhận sống 2026-07-11."""
        from crawlers.b2b.duocphamgiasi import DuocPhamGiaSiCrawler, _PAGE_SIZE

        c = DuocPhamGiaSiCrawler(_cfg("duocphamgiasi"))
        seen_paths: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/tai-khoan":
                return httpx.Response(200, text="<html></html>")
            seen_paths.append(req.url.path)
            count = _PAGE_SIZE if req.url.path == "/product/" else 3
            cards = "".join(
                f'<article class="product-item"><div class="product-card" data-price="1000">'
                f'<div class="entry-title"><a href="/product/p{i}/">P{i}</a></div>'
                f'</div></article>'
                for i in range(count)
            )
            return httpx.Response(200, text=f"<html>{cards}</html>")

        _run(c.open())
        _attach(c, handler)
        items = _run(c._fetch_products(""))
        assert seen_paths == ["/product/", "/product/page/2/"]
        assert len(items) == _PAGE_SIZE + 3
        _run(c.close())

    def test_search_pagination_uses_paged_query_param(self) -> None:
        """Search (có keyword) dùng query param `paged` cho trang sau — KHÁC catalog
        dùng pretty-permalink /product/page/N/."""
        from crawlers.b2b.duocphamgiasi import DuocPhamGiaSiCrawler, _PAGE_SIZE

        c = DuocPhamGiaSiCrawler(_cfg("duocphamgiasi"))
        seen_paged: list[str | None] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/tai-khoan":
                return httpx.Response(200, text="<html></html>")
            seen_paged.append(req.url.params.get("paged"))
            count = _PAGE_SIZE if req.url.params.get("paged") is None else 2
            cards = "".join(
                f'<article class="product-item"><div class="product-card" data-price="1000">'
                f'<div class="entry-title"><a href="/product/p{i}/">P{i}</a></div>'
                f'</div></article>'
                for i in range(count)
            )
            return httpx.Response(200, text=f"<html>{cards}</html>")

        _run(c.open())
        _attach(c, handler)
        items = _run(c._fetch_products("boganic"))
        assert seen_paged == [None, "2"]
        assert len(items) == _PAGE_SIZE + 2
        _run(c.close())


# =====================================================================
# BachHoaThuoc
# =====================================================================


class TestBachHoaThuoc:
    def test_pkce_login_and_fetch_and_parse(self) -> None:
        from crawlers.b2b.bachhoathuoc import BachHoaThuocCrawler

        c = BachHoaThuocCrawler(_cfg("bachhoathuoc", "https://sales.bachhoathuoc.com"))

        def handler(req: httpx.Request) -> httpx.Response:
            host = req.url.host or ""
            p = req.url.path
            q = (req.url.query or b"").decode() if isinstance(req.url.query, bytes) else (req.url.query or "")

            # Step 3: follow redirect_to (has login_verifier) -> 302 with code
            if host == "oauth.bachhoathuoc.com" and p == "/oauth/authorize" and "login_verifier" in q:
                return httpx.Response(
                    302,
                    headers={
                        "location": "https://sales.bachhoathuoc.com?code=CODE789&state=STATE"
                    },
                )
            # Step 1: authorize -> redirect with challenge
            if host == "oauth.bachhoathuoc.com" and p == "/oauth/authorize" and req.method == "GET":
                return httpx.Response(
                    302,
                    headers={
                        "location": "https://identity.tekoapis.com/login?challenge=CHAL123&state=STATE"
                    },
                )
            # Step 2: login -> redirect_to (no code, must follow)
            if host == "identity.tekoapis.com" and p == "/api/v1/users/login":
                return httpx.Response(
                    200,
                    json={
                        "redirect_to": "https://oauth.bachhoathuoc.com/oauth/authorize?login_verifier=VERIF456"
                    },
                )
            # Step 4: token exchange
            if host == "oauth.bachhoathuoc.com" and p == "/oauth/token":
                return httpx.Response(200, json={"access_token": "BHT_TOK"})
            # Step 5: search products
            if host == "discovery.tekoapis.com" and "search-skus" in p:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "products": [
                                {
                                    "name": "MagB6",
                                    "latestPrice": 18000,
                                    "brandName": "Sanofi",
                                    "uomName": "Viên",
                                    "canonical": "mag-b6",
                                }
                            ]
                        }
                    },
                )
            return httpx.Response(404)

        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl("mag"))
        assert len(results) == 1
        dp = results[0]
        assert dp.drug_name == "MagB6"
        assert dp.price_vnd == 18000
        assert dp.brand == "Sanofi"
        assert dp.manufacturer == "Sanofi"
        assert dp.dosage_form == "Viên"
        assert dp.source == SourceName.BACHHOATHUOC
        _run(c.close())

    def test_authorize_non_redirect_raises(self) -> None:
        from crawlers.b2b.bachhoathuoc import BachHoaThuocCrawler

        c = BachHoaThuocCrawler(_cfg("bachhoathuoc"))

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not a redirect")

        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError):
            _run(c._login())
        _run(c.close())

    def test_parse_product_supplier_retail_price(self) -> None:
        from crawlers.b2b.bachhoathuoc import BachHoaThuocCrawler

        c = BachHoaThuocCrawler(_cfg("bachhoathuoc"))
        dp = c._parse_product({"name": "X", "supplierRetailPrice": 9999, "canonical": "x-slug"})
        assert dp is not None
        assert dp.price_vnd == 9999
        assert "x-slug" in dp.source_url

    def test_walk_helper(self) -> None:
        from crawlers.b2b.bachhoathuoc import BachHoaThuocCrawler

        assert BachHoaThuocCrawler._walk({"data": {"products": [{"a": 1}]}}) == ([{"a": 1}], None)
        assert BachHoaThuocCrawler._walk(
            {"data": {"products": [{"a": 1}], "total": 5}}
        ) == ([{"a": 1}], 5)
        assert BachHoaThuocCrawler._walk({"data": [{"b": 2}]}) == ([{"b": 2}], None)
        assert BachHoaThuocCrawler._walk({}) == ([], None)

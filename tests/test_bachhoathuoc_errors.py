"""Tests nhánh lỗi PKCE của BachHoaThuocCrawler (bổ sung coverage 89% → ~100%).

Mỗi test dựng chuỗi OAuth thành công đến bước N rồi làm hỏng bước N+1,
khẳng định AuthError với message tương ứng.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from crawlers.b2b.bachhoathuoc import (
    _CATALOG_BRANDS,
    _OEM_PRICE_BUCKETS,
    _PAGE_SIZE,
    _SAFETY_MAX_PAGES,
    BachHoaThuocCrawler,
)
from crawlers.base import AuthError
from utils.models import SiteConfig


async def _noop(*_args: object, **_kwargs: object) -> None:
    return None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
    monkeypatch.setattr("crawlers.b2b.bachhoathuoc.asyncio.sleep", _noop)


def _cfg() -> SiteConfig:
    c = SiteConfig(id="bachhoathuoc")
    c.base_url = "https://sales.bachhoathuoc.com"
    c.credentials.username = "user"
    c.credentials.password = "pass"
    c.rate_limit.delay_seconds = 0.0
    c.rate_limit.retry_backoff_seconds = 0.0
    return c


def _attach(crawler: BachHoaThuocCrawler, handler) -> None:
    crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )


def _run(coro):
    return asyncio.run(coro)


def _authorize_ok(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(
        302,
        headers={"location": "https://identity.tekoapis.com/login?challenge=CHAL&state=S"},
    )


def _login_ok(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={"redirect_to": "https://oauth.bachhoathuoc.com/oauth/authorize?login_verifier=V"},
    )


def _redirect_with_code(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(
        302, headers={"location": "https://sales.bachhoathuoc.com?code=CODE&state=S"}
    )


class TestPkceErrorBranches:
    def _login_expect_error(self, handler, match: str) -> None:
        c = BachHoaThuocCrawler(_cfg())
        _run(c.open())
        _attach(c, handler)
        with pytest.raises(AuthError, match=match):
            _run(c._login())
        _run(c.close())

    def test_authorize_redirect_without_challenge(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302, headers={"location": "https://identity.tekoapis.com/login?state=S"}
            )

        self._login_expect_error(handler, "challenge")

    def test_login_http_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/v1/users/login":
                return httpx.Response(403, text="denied")
            return _authorize_ok(req)

        self._login_expect_error(handler, "Login HTTP 403")

    def test_login_missing_redirect_to(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            if req.url.path == "/api/v1/users/login":
                return httpx.Response(200, json={"ok": True})
            return _authorize_ok(req)

        self._login_expect_error(handler, "redirect_to")

    def test_redirect_chain_follows_until_code(self) -> None:
        """Redirect đầu chưa có code → follow location kế tiếp mới có code (dòng 94)."""
        seen: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            q = req.url.query.decode() if isinstance(req.url.query, bytes) else req.url.query
            if req.url.path == "/api/v1/users/login":
                return _login_ok(req)
            if "login_verifier" in q:
                seen.append("hop1")
                return httpx.Response(
                    302,
                    headers={"location": "https://oauth.bachhoathuoc.com/oauth/consent?step=2"},
                )
            if req.url.path == "/oauth/consent":
                seen.append("hop2")
                return _redirect_with_code(req)
            if req.url.path == "/oauth/token":
                return httpx.Response(200, json={"access_token": "TOK"})
            return _authorize_ok(req)

        c = BachHoaThuocCrawler(_cfg())
        _run(c.open())
        _attach(c, handler)
        _run(c._login())
        assert c._token == "TOK"
        assert seen == ["hop1", "hop2"]
        _run(c.close())

    def test_redirect_chain_non_redirect_breaks(self) -> None:
        """Redirect chain trả 200 (không phải 302) → break → AuthError (dòng 95-99)."""

        def handler(req: httpx.Request) -> httpx.Response:
            q = req.url.query.decode() if isinstance(req.url.query, bytes) else req.url.query
            if req.url.path == "/api/v1/users/login":
                return _login_ok(req)
            if "login_verifier" in q:
                return httpx.Response(200, text="login page again")
            return _authorize_ok(req)

        self._login_expect_error(handler, "code")

    def test_redirect_chain_exhausts_without_code(self) -> None:
        """5 vòng redirect đều không có code → AuthError (dòng 99)."""

        def handler(req: httpx.Request) -> httpx.Response:
            q = req.url.query.decode() if isinstance(req.url.query, bytes) else req.url.query
            if req.url.path == "/api/v1/users/login":
                return _login_ok(req)
            if "login_verifier" in q or req.url.path == "/oauth/loop":
                return httpx.Response(
                    302, headers={"location": "https://oauth.bachhoathuoc.com/oauth/loop"}
                )
            return _authorize_ok(req)

        self._login_expect_error(handler, "code")

    def test_token_exchange_http_error(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            q = req.url.query.decode() if isinstance(req.url.query, bytes) else req.url.query
            if req.url.path == "/api/v1/users/login":
                return _login_ok(req)
            if "login_verifier" in q:
                return _redirect_with_code(req)
            if req.url.path == "/oauth/token":
                return httpx.Response(400, text="invalid_grant")
            return _authorize_ok(req)

        self._login_expect_error(handler, "Token exchange HTTP 400")

    def test_token_response_missing_access_token(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            q = req.url.query.decode() if isinstance(req.url.query, bytes) else req.url.query
            if req.url.path == "/api/v1/users/login":
                return _login_ok(req)
            if "login_verifier" in q:
                return _redirect_with_code(req)
            if req.url.path == "/oauth/token":
                return httpx.Response(200, json={"token_type": "Bearer"})
            return _authorize_ok(req)

        self._login_expect_error(handler, "access_token")


class TestPagination:
    def test_full_page_fetches_next(self) -> None:
        """Trang 1 đủ _PAGE_SIZE → tiếp trang 2; trang 2 thiếu → dừng (dòng 154-155)."""
        pages: list[int] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if "search-skus" in req.url.path:
                import json

                page = json.loads(req.content.decode())["page"]
                pages.append(page)
                count = _PAGE_SIZE if page == 1 else 3
                return httpx.Response(
                    200,
                    json={"data": {"products": [
                        {"name": f"P{page}-{i} kw", "latestPrice": 1000} for i in range(count)
                    ]}},
                )
            return httpx.Response(404)

        c = BachHoaThuocCrawler(_cfg())
        c._token = "TOK"
        _run(c.open())
        _attach(c, handler)
        products = _run(c._fetch_products("kw"))
        assert pages == [1, 2]
        assert len(products) == _PAGE_SIZE + 3
        _run(c.close())

    def test_keyword_not_confirmed_by_server_filters_client_side(self) -> None:
        """search-skus-v2 không confirm filter theo keyword (chỉ `slug`) — server có thể
        trả về nguyên catalog; crawler phải tự lọc theo keyword trong tên sản phẩm."""

        def handler(req: httpx.Request) -> httpx.Response:
            if "search-skus" in req.url.path:
                return httpx.Response(
                    200,
                    json={"data": {"products": [
                        {"name": "Boganic hộp 10 vỉ", "latestPrice": 1000},
                        {"name": "Paracetamol 500mg", "latestPrice": 2000},
                    ]}},
                )
            return httpx.Response(404)

        c = BachHoaThuocCrawler(_cfg())
        c._token = "TOK"
        _run(c.open())
        _attach(c, handler)
        products = _run(c._fetch_products("boganic"))
        assert len(products) == 1
        assert products[0]["name"] == "Boganic hộp 10 vỉ"
        _run(c.close())

    def test_max_pages_cap_stops_runaway_pagination(self) -> None:
        """Server luôn trả đủ _PAGE_SIZE, không có field `total` (không lọc theo
        keyword) → phải dừng ở _SAFETY_MAX_PAGES thay vì chạy vô hạn (chống hang +
        spam request)."""
        pages: list[int] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if "search-skus" in req.url.path:
                import json

                page = json.loads(req.content.decode())["page"]
                pages.append(page)
                return httpx.Response(
                    200,
                    json={"data": {"products": [
                        {"name": f"P{page}-{i} kw", "latestPrice": 1000}
                        for i in range(_PAGE_SIZE)
                    ]}},
                )
            return httpx.Response(404)

        c = BachHoaThuocCrawler(_cfg())
        c._token = "TOK"
        _run(c.open())
        _attach(c, handler)
        _run(c._fetch_products("kw"))
        assert pages == list(range(1, _SAFETY_MAX_PAGES + 1))
        _run(c.close())

    def test_total_field_stops_pagination_early(self) -> None:
        """Response có `total` → dừng đúng lúc đã fetch đủ, không cần trang cuối hụt
        (vd catalog 90 sản phẩm, pageSize 40 → 3 trang dù trang 3 cũng đủ 40)."""
        pages: list[int] = []

        def handler(req: httpx.Request) -> httpx.Response:
            if "search-skus" in req.url.path:
                import json

                page = json.loads(req.content.decode())["page"]
                pages.append(page)
                return httpx.Response(
                    200,
                    json={"data": {
                        "total": 90,
                        "products": [
                            {"name": f"P{page}-{i} kw", "latestPrice": 1000}
                            for i in range(_PAGE_SIZE)
                        ],
                    }},
                )
            return httpx.Response(404)

        c = BachHoaThuocCrawler(_cfg())
        c._token = "TOK"
        _run(c.open())
        _attach(c, handler)
        products = _run(c._fetch_products("kw"))
        # `total` chỉ dùng để biết KHI NÀO dừng gọi thêm trang (không đòi hỏi server
        # trả đúng phần dư ở trang cuối) — điểm cần khẳng định là dừng ở trang 3,
        # không tiếp tục gọi trang 4 dù mỗi trang đều đủ _PAGE_SIZE.
        assert pages == [1, 2, 3]
        assert len(products) == 3 * _PAGE_SIZE
        _run(c.close())


class TestCrawlAll:
    def test_partitions_by_brand_and_dedupes_by_sku(self) -> None:
        """crawl_all() chia theo BRAND (`filter.brands`) — mỗi brand server lọc thật
        (khác keyword bị bỏ qua), phủ catalog đầy đủ hơn category (xác nhận sống:
        9-12 category chỉ phủ ~71%, 44 brand phủ ~101% raw trước dedup) — và khử
        trùng theo `sku` khi 1 sản phẩm xuất hiện ở nhiều brand."""
        requests_seen: list[str] = []
        # sku "DUP" cố tình xuất hiện ở 2 brand đầu (không phải "oem") để test dedup.
        b0, b1 = _CATALOG_BRANDS[0], _CATALOG_BRANDS[1]
        assert "oem" not in (b0, b1)
        by_brand = {
            b0: [
                {"sku": "DUP", "name": "Trùng lặp", "latestPrice": 1000},
                {"sku": "A1", "name": "Sản phẩm A1", "latestPrice": 2000},
            ],
            b1: [
                {"sku": "DUP", "name": "Trùng lặp", "latestPrice": 1000},
                {"sku": "B1", "name": "Sản phẩm B1", "latestPrice": 3000},
            ],
        }

        def handler(req: httpx.Request) -> httpx.Response:
            import json

            if "search-skus" not in req.url.path:
                return httpx.Response(404)
            body = json.loads(req.content.decode())
            brand = body["filter"]["brands"][0]
            requests_seen.append(brand)
            items = by_brand.get(brand, [])
            return httpx.Response(200, json={"data": {"products": items}})

        c = BachHoaThuocCrawler(_cfg())
        c._token = "TOK"
        c._authenticated = True
        c._auth_time = time.time()
        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl_all())
        _run(c.close())

        # Mọi brand đều được gọi (kể cả brand rỗng trong mock); "oem" gọi thêm 1
        # lần cho mỗi khoảng giá (chia nhỏ do vượt cap page*pageSize<=5000).
        expected_calls = len(_CATALOG_BRANDS) - 1 + len(_OEM_PRICE_BUCKETS)
        assert len(requests_seen) == expected_calls
        assert set(requests_seen) == set(_CATALOG_BRANDS)
        # 3 sku duy nhất (DUP tính 1 lần) dù xuất hiện ở 2 brand.
        assert sorted(p.product_id for p in results) == ["A1", "B1", "DUP"]
        assert len(results) == 3

    def test_oem_brand_split_by_price_buckets(self) -> None:
        """Brand "oem" vượt cap page*pageSize<=5000 — chia nhỏ theo khoảng giá,
        mỗi request phải mang đúng `priceGte`/`priceLte` của bucket đó."""
        seen_ranges: list[tuple[str, str]] = []

        def handler(req: httpx.Request) -> httpx.Response:
            import json

            if "search-skus" not in req.url.path:
                return httpx.Response(404)
            body = json.loads(req.content.decode())
            filt = body["filter"]
            if filt.get("brands") == ["oem"]:
                seen_ranges.append((filt["priceGte"], filt["priceLte"]))
                return httpx.Response(
                    200,
                    json={"data": {"products": [
                        {"sku": f"OEM-{filt['priceGte']}", "name": "X", "latestPrice": 1}
                    ]}},
                )
            return httpx.Response(200, json={"data": {"products": []}})

        c = BachHoaThuocCrawler(_cfg())
        c._token = "TOK"
        c._authenticated = True
        c._auth_time = time.time()
        _run(c.open())
        _attach(c, handler)
        results = _run(c.crawl_all())
        _run(c.close())

        assert seen_ranges == [(str(lo), str(hi)) for lo, hi in _OEM_PRICE_BUCKETS]
        oem_results = [p for p in results if p.product_id.startswith("OEM-")]
        assert len(oem_results) == len(_OEM_PRICE_BUCKETS)


class TestFetchPriceById:
    def test_merges_productinfo_and_prices_then_parses(self) -> None:
        """Response thật (xác nhận live 2026-07-11): tên/canonical/uomName nằm trong
        `productInfo`, giá nằm ở `prices[0]` (list riêng) — không nằm cùng cấp như
        listing endpoint. fetch_price_by_id phải merge lại trước khi parse."""
        def handler(req: httpx.Request) -> httpx.Response:
            assert req.url.path == "/api/v1/products"
            assert req.url.params["skus"] == "220900029"
            assert req.url.params["terminalCode"] == "350_OLN_WEB_0001"
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "result": {
                        "products": [
                            {
                                "productInfo": {
                                    "sku": "220900029",
                                    "name": "KĐ.Daehwa Almetamin",
                                    "canonical": "almetamin-hq--s220900029",
                                    "uomName": "Hộp",
                                    "brand": {"name": "OEM"},
                                },
                                "prices": [{"latestPrice": "70000"}],
                            }
                        ]
                    },
                },
            )

        c = BachHoaThuocCrawler(_cfg())
        c._token = "TOK"
        c._authenticated = True
        c._auth_time = time.time()
        _run(c.open())
        _attach(c, handler)
        price = _run(c.fetch_price_by_id("220900029"))
        _run(c.close())

        assert price is not None
        assert price.drug_name == "KĐ.Daehwa Almetamin"
        assert price.price_vnd == 70000
        assert price.product_id == "220900029"
        assert price.brand == "OEM"

    def test_returns_none_when_no_products(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"code": "0", "result": {"products": []}})

        c = BachHoaThuocCrawler(_cfg())
        c._token = "TOK"
        c._authenticated = True
        c._auth_time = time.time()
        _run(c.open())
        _attach(c, handler)
        price = _run(c.fetch_price_by_id("missing-sku"))
        _run(c.close())

        assert price is None

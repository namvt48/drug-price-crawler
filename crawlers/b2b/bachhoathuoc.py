"""Sales.BachHoaThuoc.com — Next.js + Teko APIs, OAuth 2.0 (Auth Code + PKCE).

Nguồn: docs/bachhoathuoc.md.
- Auth: OAuth PKCE: authorize → identity.tekoapis.com login → exchange token.
- Product: POST discovery.tekoapis.com/api/v2/search-skus-v2 (Bearer + userId).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from typing import Any
from urllib.parse import parse_qs, urlparse

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price
from utils.stock_status import detect_stock_status

from ..base import AuthError, BaseCrawler

DISCOVERY = "https://discovery.tekoapis.com"
OAUTH = "https://oauth.bachhoathuoc.com"
IDENTITY_API = "https://identity.tekoapis.com"
CLIENT_ID = "555a7a17030d471da7f7d6a5029318e5"
REDIRECT_URI = "https://sales.bachhoathuoc.com"
TERMINAL_ID = 289
TERMINAL_CODE = "350_OLN_WEB_0001"
PLATFORM_ID = 21
USER_ID = "2538a989bd074055b2ed7b66c802e377"
_PAGE_SIZE = 40
# Chặn cứng an toàn: search-skus-v2 không có filter theo keyword (chỉ `slug` category,
# xem docs/bachhoathuoc.md §3.1) nên trả nguyên catalog (~10.439 sp / 40 = 261 trang,
# xác nhận live 2026-07-11) dù truyền keyword gì. Vòng lặp dừng đúng lúc dựa vào field
# `total` trong response; cap này chỉ là backstop nếu `total` thiếu/sai — không dùng để
# giới hạn data thật (user cần full catalog để cache).
_SAFETY_MAX_PAGES = 300
# search-skus-v2 CÓ lọc theo `slug` (category) VÀ `filter.brands` — không như
# `keyword` bị bỏ qua. Đã thử chia catalog theo 9-12 category (docs/bachhoathuoc.md
# §3.3 cũ) nhưng nhiều sản phẩm KHÔNG gắn category nào — chỉ phủ được ~71%
# (7.416/10.439, xác nhận sống 2026-07-11). Chuyển sang chia theo BRAND
# (`filter.brands`, xác nhận sống: tổng 44 brand = 10.520, sát 10.439 thật — dimension
# đầy đủ hơn nhiều category) — xem `docs/bachhoathuoc.md` §3.3b.
_CATALOG_BRANDS = [
    "100plus", "an-lanh", "astrazaneca", "be-fresh", "boston", "cdat", "cdbht",
    "cong-ty-co-phan-duoc-vtyt-hai-duong", "cpc1", "cvi", "davi-pharm", "duoc-hau-giang",
    "duoc-pham-truong-tho", "flormar", "gsk", "hang-chien-luoc", "hang-clgt", "hang-tu-van",
    "hasan", "johnson-&-johnson", "khac", "luc-lam", "mebiphar", "medipharco", "msd",
    "nam-duoc", "oem", "og-care", "others", "pixobitz", "pymepharco", "rohto", "savipharm",
    "stella", "techland", "thanh-cong", "thephaco", "tin-phong", "tipharco", "tohe",
    "truong-son", "tue-duc", "usp", "vinphaco",
]
# Brand "oem" một mình vượt cap page*pageSize<=5000 (xác nhận sống: 6.816 sản phẩm)
# — chia nhỏ thêm theo khoảng giá (mọi sản phẩm đều có giá, dimension đầy đủ hơn
# category). Buckets đủ hẹp để mỗi khoảng nằm dưới cap; có thể còn sót 1 phần nhỏ
# do biên giá/sản phẩm giá null — chấp nhận được so với việc bỏ hẳn oem.
_OEM_PRICE_BUCKETS = [
    (0, 20000), (20000, 40000), (40000, 60000), (60000, 90000),
    (90000, 130000), (130000, 180000), (180000, 250000), (250000, 400000),
    (400000, 700000), (700000, 1500000), (1500000, 999999999),
]


class BachHoaThuocCrawler(BaseCrawler):
    source_name = SourceName.BACHHOATHUOC
    direct_fetch_supported = True
    # Server bỏ qua "keyword" (chỉ hỗ trợ `slug` category) — CrawlerEngine sẽ cache
    # toàn catalog 1 lần (TTL dài) rồi lọc theo keyword thật, thay vì quét lại 261
    # trang mỗi lần search.
    keyword_search_supported = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._user_id = USER_ID

    async def _login(self) -> None:
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).rstrip(b"=").decode()
        state = secrets.token_urlsafe(16)

        authorize_url = (
            f"{OAUTH}/oauth/authorize?response_type=code"
            f"&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
            f"&code_challenge={challenge}&code_challenge_method=S256&state={state}"
        )
        resp = await self.request_with_retry("GET", authorize_url, allow_reauth=False)
        if resp.status_code not in (302, 303):
            raise AuthError(f"OAuth authorize HTTP {resp.status_code}: {resp.text[:200]}")
        location = resp.headers.get("location", "")
        parsed = urlparse(location)
        qs = parse_qs(parsed.query)
        challenge_param = (qs.get("challenge") or qs.get("login_challenge") or [""])[0]
        if not challenge_param:
            raise AuthError(f"Không lấy được challenge từ authorize redirect: {location[:200]}")

        await asyncio.sleep(1)

        login_resp = await self.request_with_retry(
            "POST",
            f"{IDENTITY_API}/api/v1/users/login",
            allow_reauth=False,
            json={
                "challenge": challenge_param,
                "username": self.config.credentials.username,
                "password": self.config.credentials.password,
                "client_id": CLIENT_ID,
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if login_resp.status_code != 200:
            raise AuthError(f"Login HTTP {login_resp.status_code}: {login_resp.text[:200]}")
        login_body = login_resp.json() or {}
        redirect_to = login_body.get("redirect_to") or login_body.get("redirect") or ""
        if not redirect_to:
            raise AuthError(f"Login response không có redirect_to: {str(login_body)[:200]}")

        await asyncio.sleep(1)

        code = ""
        for _ in range(5):
            redir_resp = await self.request_with_retry("GET", redirect_to, allow_reauth=False)
            if redir_resp.status_code in (302, 303):
                location = redir_resp.headers.get("location", "")
                parsed_redir = urlparse(location)
                code = (parse_qs(parsed_redir.query).get("code") or [""])[0]
                if code:
                    break
                redirect_to = location
            else:
                break
            await asyncio.sleep(0.5)
        if not code:
            raise AuthError(f"Không lấy được code từ redirect chain: {redirect_to[:200]}")

        await asyncio.sleep(1)

        token_resp = await self.request_with_retry(
            "POST",
            f"{OAUTH}/oauth/token",
            allow_reauth=False,
            data={
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_resp.status_code != 200:
            raise AuthError(f"Token exchange HTTP {token_resp.status_code}: {token_resp.text[:200]}")
        token_body = token_resp.json() or {}
        self._token = token_body.get("access_token") or ""
        if not self._token:
            raise AuthError(f"Không lấy được access_token: {str(token_body)[:200]}")

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

    async def _fetch_products(self, keyword: str) -> list[dict]:
        return await self._fetch_paginated(
            {"keyword": keyword}, keyword_norm=keyword.strip().lower()
        )

    async def _fetch_products_by_brand(
        self, brand: str, price_range: tuple[int, int] | None = None
    ) -> list[dict]:
        """Như `_fetch_products` nhưng lọc theo `filter.brands` — server CÓ áp dụng
        field này (khác `keyword` bị bỏ qua), dùng cho `crawl_all()` để mỗi request
        nằm dưới cap page*pageSize<=5000. `price_range` dùng để chia nhỏ thêm brand
        quá lớn (vd "oem", xem `_OEM_PRICE_BUCKETS`)."""
        filt: dict[str, Any] = {"brands": [brand]}
        if price_range is not None:
            lo, hi = price_range
            filt["priceGte"] = str(lo)
            filt["priceLte"] = str(hi)
        return await self._fetch_paginated({"filter": filt})

    async def _fetch_paginated(self, extra_body: dict, keyword_norm: str = "") -> list[dict]:
        """Vòng lặp phân trang chung cho search-skus-v2. `extra_body` chứa field lọc
        khác nhau tuỳ chế độ gọi (`keyword` — server bỏ qua, cần lọc lại phía client;
        hoặc `slug` — server lọc thật, không cần lọc thêm)."""
        products: list[dict] = []
        page = 1
        total: int | None = None
        while page <= _SAFETY_MAX_PAGES:
            resp = await self.request_with_retry(
                "POST",
                f"{DISCOVERY}/api/v2/search-skus-v2",
                json={
                    "terminalId": TERMINAL_ID,
                    "page": page,
                    "pageSize": _PAGE_SIZE,
                    "filter": {},
                    "sorting": {"sort": "SORT_BY_UNSPECIFIED", "order": "ORDER_BY_UNSPECIFIED"},
                    "returnFilterable": [],
                    "isNeedFeaturedProducts": False,
                    "userId": self._user_id,
                    **extra_body,
                },
                headers=self._headers(),
            )
            body = resp.json() or {}
            batch, page_total = self._walk(body)
            if total is None and page_total is not None:
                total = page_total
            if keyword_norm:
                # API không confirm hỗ trợ filter theo "keyword" nên tự lọc phía
                # client để tránh trả về cả catalog không liên quan.
                products.extend(p for p in batch if keyword_norm in (p.get("name") or "").lower())
            else:
                products.extend(batch)
            fetched = page * _PAGE_SIZE
            if len(batch) < _PAGE_SIZE or (total is not None and fetched >= total):
                break
            page += 1
            await self._throttle()
        else:
            self.log(f"Đạt giới hạn an toàn {_SAFETY_MAX_PAGES} trang — dừng.")
        return products

    async def crawl_all(self) -> list[DrugPrice]:
        """Toàn bộ catalog CÓ giá, chia theo BRAND (`_CATALOG_BRANDS`) để vượt qua
        cap page*pageSize<=5000 của 1 request duy nhất — gom + khử trùng theo `sku`.
        Brand phủ catalog đầy đủ hơn category (xem ghi chú tại `_CATALOG_BRANDS`);
        brand "oem" (quá lớn) được chia nhỏ thêm theo khoảng giá."""
        await self.ensure_auth()
        seen_skus: set[str] = set()
        results: list[DrugPrice] = []

        def _absorb(raw_items: list[dict]) -> int:
            added = 0
            for raw in raw_items:
                sku = str(raw.get("sku") or "")
                if sku:
                    if sku in seen_skus:
                        continue
                    seen_skus.add(sku)
                try:
                    price = self._parse_product(raw)
                    if price is not None and price.drug_name:
                        results.append(price)
                        added += 1
                except Exception as exc:  # 1 item hỏng không được làm hỏng cả mẻ
                    self.log(f"Bỏ qua 1 sản phẩm lỗi parse: {exc}")
            return added

        for brand in _CATALOG_BRANDS:
            if brand == "oem":
                for lo, hi in _OEM_PRICE_BUCKETS:
                    raw_items = await self._fetch_products_by_brand(brand, (lo, hi))
                    added = _absorb(raw_items)
                    self.log(f"[brand={brand} {lo}-{hi}] +{added} sản phẩm (tổng {len(results)}).")
            else:
                raw_items = await self._fetch_products_by_brand(brand)
                added = _absorb(raw_items)
                self.log(f"[brand={brand}] +{added} sản phẩm (tổng {len(results)}).")
        self.log(f"Catalog: {len(results)} sản phẩm ({len(_CATALOG_BRANDS)} brand).")
        return results

    async def fetch_price_by_id(self, product_id: str) -> DrugPrice | None:
        """Giá LIVE cho đúng 1 SKU — dùng khi user chọn 1 sản phẩm cụ thể từ catalog,
        không qua crawl_all()/cache. Response endpoint chi tiết KHÁC shape so với
        listing: tên/canonical/uomName nằm trong `productInfo`, giá nằm ở `prices[0]`
        (list riêng) — merge lại trước khi đưa vào `_parse_product` (xác nhận live
        2026-07-11, xem docs/bachhoathuoc.md §3.2)."""
        await self.ensure_auth()
        resp = await self.request_with_retry(
            "GET",
            f"{DISCOVERY}/api/v1/products",
            params={"skus": product_id, "terminalCode": TERMINAL_CODE},
            headers=self._headers(),
        )
        body = resp.json() or {}
        products = ((body.get("result") or {}).get("products")) or []
        if not products:
            return None
        entry = products[0]
        info = entry.get("productInfo") or {}
        prices = entry.get("prices") or [{}]
        merged = {
            **info,
            **(prices[0] if prices else {}),
            "brandName": (info.get("brand") or {}).get("name") or "",
        }
        return self._parse_product(merged)

    @staticmethod
    def _walk(body: Any) -> tuple[list[dict], int | None]:
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict) and isinstance(data.get("products"), list):
            total = data.get("total")
            return data["products"], (total if isinstance(total, int) else None)
        if isinstance(data, list):
            return data, None
        return [], None

    def _parse_product(self, raw: dict) -> DrugPrice | None:
        name = raw.get("name") or ""
        price = parse_price(
            raw.get("latestPrice")
            or raw.get("supplierRetailPrice")
            or raw.get("minLatestPrice")
            or 0
        )
        canonical = raw.get("canonical") or raw.get("slug") or ""
        return DrugPrice(
            drug_name=name,
            brand=raw.get("brandName") or "",
            manufacturer=raw.get("brandName") or "",
            dosage_form=raw.get("uomName") or "",
            price_vnd=price,
            price_display=format_price(price),
            stock_status=detect_stock_status(raw),
            source=self.source_name,
            source_url=f"{self.config.base_url}/{canonical}" if canonical else self.config.base_url,
            product_id=str(raw.get("sku") or ""),
        )

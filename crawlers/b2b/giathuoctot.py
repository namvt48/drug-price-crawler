"""Giathuoctot.com — Angular SPA + JSON API, auth JWT Bearer.

Nguồn tài liệu: docs/giathuoctot.md (đã confirm).
- Login:  POST api/authentication/account/v2/login {userName, password}
- Search: POST api/product/product/search-products-client {limit, offset, searchTerm}
- Giá chỉ hiện với endpoint -client + Bearer token (guest trả basePrice=0).
"""

from __future__ import annotations

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price

from ..base import AuthError, BaseCrawler

API = "https://api.giathuoctot.com"
_PAGE = 200  # max limit per request (API tối đa 200)


class GiathuoctotCrawler(BaseCrawler):
    source_name = SourceName.GIATHUOCTOT

    def _api_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "from": "Web",
            "source": "FE",
            "Authorization": f"Bearer {self._token}",
        }

    async def _login(self) -> None:
        resp = await self.request_with_retry(
            "POST",
            f"{API}/authentication/account/v2/login",
            allow_reauth=False,
            json={
                "userName": self.config.credentials.username,
                "password": self.config.credentials.password,
            },
            headers={"Content-Type": "application/json", "from": "Web", "source": "FE"},
        )
        if resp.status_code != 200:
            raise AuthError(f"Login HTTP {resp.status_code}: {resp.text[:150]}")
        data = (resp.json() or {}).get("data") or {}
        self._token = data.get("jwtToken", "")
        if not self._token:
            raise AuthError("Không lấy được jwtToken.")

    async def _fetch_products(self, keyword: str) -> list[dict]:
        products: list[dict] = []
        offset = 0
        while True:
            resp = await self.request_with_retry(
                "POST",
                f"{API}/product/product/search-products-client",
                json={"limit": _PAGE, "offset": offset, "searchTerm": keyword},
                headers=self._api_headers(),
            )
            body = resp.json() or {}
            batch = body.get("products", []) or []
            products.extend(batch)
            total = int(body.get("total", 0) or 0)
            offset += _PAGE
            if not batch or offset >= total:
                break
            await self._throttle()
        return products

    def _parse_product(self, raw: dict) -> DrugPrice | None:
        price = parse_price(raw.get("basePrice") or raw.get("pricingTablePrice") or 0)
        manufacturer = (raw.get("manufacturer") or {}).get("name", "")
        slug = raw.get("slug", "")
        image_urls = raw.get("imageUrls") or []
        image_url = image_urls[0] if image_urls else ""
        return DrugPrice(
            drug_name=raw.get("name", ""),
            brand=manufacturer,
            manufacturer=manufacturer,
            dosage_form=raw.get("retailUnit", ""),
            price_vnd=price,
            price_display=format_price(price),
            source=self.source_name,
            source_url=f"{self.config.base_url}/product/{slug}" if slug else self.config.base_url,
            product_id=slug or str(raw.get("id", "")),
            image_url=image_url,
        )

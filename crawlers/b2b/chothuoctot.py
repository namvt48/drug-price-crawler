"""ChoThuocTot.vn — Next.js + Medlink API (api.medlink.vn), auth Bearer.

Nguồn: docs/chothuoctot.md + reverse-engineer trực tiếp 2026-07-11 (endpoint/login
đúng, nhưng `_parse_product` map SAI tên field so với response thật — mọi kết quả
bị lọc mất im lặng vì `drug_name` luôn rỗng, dù API trả đúng dữ liệu).
- Login:  POST api.medlink.vn/oauth/token (Basic Auth medlink:kidsecret, FormData grant_type=password)
- Search: GET pharmacy/supply/search-product?product_name&page&size (Bearer)
- Field thật: tên ở `drg_drug_name` (không phải `product_name`/`name`), id ở
  `drug_id`, quy cách ở `package_desc`, NSX ở `company_name`. Giá KHÔNG nằm ở
  field phẳng — nằm trong `units[0].price`/`units[0].wholesale_price` (1 sản
  phẩm có thể có nhiều đơn vị tính, lấy đơn vị đầu tiên).
"""

from __future__ import annotations

from typing import Any

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price

from ..base import AuthError, BaseCrawler

API = "https://api.medlink.vn"
OAUTH_BASIC = "bWVkbGluazpraWRzZWNyZXQ="  # base64("medlink:kidsecret")
_SIZE = 20


def _walk(body: Any) -> list[dict]:
    if isinstance(body, dict):
        for key in ("data", "content", "products", "items", "result"):
            val = body.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict) and isinstance(val.get("content"), list):
                return val["content"]
    return []


class ChoThuocTotCrawler(BaseCrawler):
    source_name = SourceName.CHOTHUOCTOT

    async def _login(self) -> None:
        resp = await self.request_with_retry(
            "POST",
            f"{API}/oauth/token",
            allow_reauth=False,
            data={
                "grant_type": "password",
                "username": self.config.credentials.username,
                "password": self.config.credentials.password,
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Authorization": f"Basic {OAUTH_BASIC}",
            },
        )
        if resp.status_code != 200:
            raise AuthError(f"Login HTTP {resp.status_code}: {resp.text[:200]}")
        body = resp.json() or {}
        self._token = body.get("access_token") or body.get("accessToken") or ""
        if not self._token:
            raise AuthError(f"Không lấy được access_token. Response: {str(body)[:200]}")

    async def _fetch_products(self, keyword: str) -> list[dict]:
        products: list[dict] = []
        page = 1
        while True:
            resp = await self.request_with_retry(
                "GET",
                f"{API}/pharmacy/supply/search-product",
                params={
                    "page": page,
                    "size": _SIZE,
                    "product_name": keyword,
                    "company_id": 0,
                    "pinned": "false",
                },
                headers={"Accept": "application/json", "Authorization": f"Bearer {self._token}"},
            )
            batch = _walk(resp.json() or {})
            products.extend(batch)
            if len(batch) < _SIZE:
                break
            page += 1
            await self._throttle()
        return products

    def _parse_product(self, raw: dict) -> DrugPrice | None:
        name = raw.get("drg_drug_name") or raw.get("product_name") or raw.get("name") or ""
        units = raw.get("units")
        unit0 = units[0] if isinstance(units, list) and units else {}
        price = parse_price(
            unit0.get("wholesale_price")
            or unit0.get("price")
            or raw.get("price")
            or raw.get("wholesale_price")
            or raw.get("sale_price")
            or 0
        )
        pid = raw.get("drug_id") or raw.get("id") or raw.get("product_id") or ""
        return DrugPrice(
            drug_name=name,
            manufacturer=raw.get("company_name") or raw.get("supplier_name") or raw.get("manufacturer") or "",
            dosage_form=raw.get("package_desc") or raw.get("unit") or raw.get("packing") or "",
            price_vnd=price,
            price_display=format_price(price),
            source=self.source_name,
            source_url=f"{self.config.base_url}/san-pham?id={pid}" if pid else self.config.base_url,
            product_id=str(pid),
        )

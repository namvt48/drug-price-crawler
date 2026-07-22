"""ChoThuoc247.vn — Laravel, session cookie + CSRF token, response JSON.

Nguồn: docs/chothuoc247.md (đã confirm).
- Login:  GET /dang-nhap.html (lấy _token từ <meta csrf-token>) → POST /submitLoginCustomer
- Search: POST /searchProduct {_token, page, search} → JSON {data, totalPages}
Session hết hạn = redirect về /dang-nhap.html hoặc HTTP 419 (CSRF).
"""

from __future__ import annotations

import httpx
from selectolax.parser import HTMLParser

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price
from utils.stock_status import detect_stock_status

from ..base import AuthError, BaseCrawler

BASE = "https://chothuoc247.vn"


def _extract_csrf(html: str) -> str:
    tree = HTMLParser(html)
    meta = tree.css_first('meta[name="csrf-token"]')
    if meta:
        return meta.attributes.get("content", "") or ""
    hidden = tree.css_first('input[name="_token"]')
    return (hidden.attributes.get("value", "") if hidden else "") or ""


class ChoThuoc247Crawler(BaseCrawler):
    source_name = SourceName.CHOTHUOC247
    direct_fetch_supported = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._csrf = ""

    def _is_auth_error(self, resp: httpx.Response) -> bool:
        if resp.status_code in (401, 403, 419):
            return True
        loc = resp.headers.get("location", "")
        return resp.status_code in (301, 302, 307) and "dang-nhap" in loc

    def _extra_auth_state(self) -> dict:
        # CSRF token gắn liền với session cookie (Laravel) — phải cache và
        # phục hồi CÙNG NHAU, thiếu 1 trong 2 là lỗi HTTP 419 dù cookie vẫn
        # còn hạn (xem crawlers/base.py `_extra_auth_state`).
        return {"csrf": self._csrf}

    def _restore_extra_auth_state(self, extra: dict) -> None:
        self._csrf = extra.get("csrf", "")

    async def _login(self) -> None:
        page = await self.request_with_retry("GET", f"{BASE}/dang-nhap.html", allow_reauth=False)
        self._csrf = _extract_csrf(page.text)
        if not self._csrf:
            raise AuthError("Không tìm thấy CSRF token ở trang đăng nhập.")

        resp = await self.request_with_retry(
            "POST",
            f"{BASE}/submitLoginCustomer",
            allow_reauth=False,
            data={
                "_token": self._csrf,
                "phone": self.config.credentials.username,
                "password": self.config.credentials.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": f"{BASE}/dang-nhap.html"},
        )
        # 302 -> /dat-hang.html = login OK. 200 + form = sai credentials.
        if resp.status_code not in (200, 302):
            raise AuthError(f"Login HTTP {resp.status_code}.")

        # Lấy _token mới từ trang đặt hàng để dùng cho searchProduct.
        order_page = await self.request_with_retry("GET", f"{BASE}/dat-hang.html", allow_reauth=False)
        fresh = _extract_csrf(order_page.text)
        if fresh:
            self._csrf = fresh

    async def _fetch_products(self, keyword: str) -> list[dict]:
        products: list[dict] = []
        page = 1
        while True:
            resp = await self.request_with_retry(
                "POST",
                f"{BASE}/searchProduct",
                data={"_token": self._csrf, "page": page, "search": keyword, "producerId": "", "categoryId": ""},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": f"{BASE}/dat-hang.html",
                },
            )
            body = resp.json() or {}
            batch = body.get("data", []) or []
            products.extend(batch)
            total_pages = int(body.get("totalPages", 1) or 1)
            if page >= total_pages or not batch:
                break
            page += 1
            await self._throttle()
        return products

    def _parse_product(self, raw: dict) -> DrugPrice | None:
        price = parse_price(raw.get("price") or 0)
        pid = raw.get("id", "")
        image = raw.get("image", "") or ""
        image_url = f"{BASE}/image/{image}" if image else ""
        return DrugPrice(
            drug_name=raw.get("name", ""),
            dosage_form=raw.get("unit", ""),
            strength=raw.get("web_volume", ""),
            price_vnd=price,
            price_display=format_price(price),
            stock_status=detect_stock_status(raw),
            source=self.source_name,
            source_url=f"{BASE}/san-pham/{pid}" if pid else BASE,
            product_id=str(pid),
            image_url=image_url,
        )

    async def fetch_price_by_id(self, product_id: str) -> DrugPrice | None:
        """Lấy đúng trang chi tiết ``/san-pham/{id}.html`` đã lưu trong catalog."""
        if not product_id:
            return None
        await self.ensure_auth()
        url = f"{BASE}/san-pham/{product_id}.html"
        resp = await self.request_with_retry("GET", url)
        if resp.status_code != 200:
            return None
        tree = HTMLParser(resp.text)
        name_node = tree.css_first(".product-title") or tree.css_first("h1")
        price_node = tree.css_first(".price")
        out_node = tree.css_first(".out-of-stock")
        stock_node = out_node or tree.css_first(".stock") or tree.css_first(".availability")
        name = name_node.text(strip=True) if name_node else ""
        if not name:
            return None
        price = parse_price(price_node.text(strip=True) if price_node else "")
        stock_text = (
            "out_of_stock" if out_node is not None else stock_node.text(strip=True) if stock_node else ""
        )
        return DrugPrice(
            drug_name=name,
            price_vnd=price,
            price_display=format_price(price),
            stock_status=detect_stock_status(text=stock_text),
            source=self.source_name,
            source_url=url,
            product_id=str(product_id),
        )

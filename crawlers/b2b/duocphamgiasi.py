"""DuocPhamGiaSi.vn — WordPress + WooCommerce (theme tuỳ biến), session cookie +
nonce, HTML-only.

Nguồn: reverse-engineer trực tiếp 2026-07-11 (theme site đã đổi so với lần capture
gốc — selector WooCommerce mặc định (`li.product`) không còn khớp, trả 0 sản phẩm
IM LẶNG suốt — không phải do site không bán, do parser lỗi thời).
- Login: GET /tai-khoan (lấy mbup_key + nonce_rwmb-user-login) → POST /tai-khoan
- Search: GET /?post_type=product&s={keyword}&paged={n>1} → parse `article.product-item`.
- Catalog (không keyword): GET /product/ (KHÔNG phải /shop/ — 404), trang sau
  dùng pretty-permalink `/product/page/{n}/` (khác search dùng query `paged`).
- Page size xác nhận = 15 (cả search lẫn catalog).
- Giá: KHÔNG lấy từ text ".price" (thường rỗng) — lấy từ attribute `data-price`
  trên `div.product-card` (JS cart dùng), phản ánh đúng "0" khi guest / giá thật
  khi đã login.
"""

from __future__ import annotations

from selectolax.parser import HTMLParser

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price

from ..base import AuthError, BaseCrawler

BASE = "https://duocphamgiasi.vn"
_PAGE_SIZE = 15
# Chặn cứng an toàn: nếu site đổi page-size hoặc phân trang lại im lặng không hoạt
# động (từng gặp ở tham số `page` sai — xem lịch sử), không để vòng lặp chạy vô hạn.
_SAFETY_MAX_PAGES = 200


def _input_value(tree: HTMLParser, name: str) -> str:
    node = tree.css_first(f'input[name="{name}"]')
    return (node.attributes.get("value", "") if node else "") or ""


class DuocPhamGiaSiCrawler(BaseCrawler):
    source_name = SourceName.DUOCPHAMGIASI

    async def _login(self) -> None:
        page = await self.request_with_retry("GET", f"{BASE}/tai-khoan", allow_reauth=False)
        tree = HTMLParser(page.text)
        mbup_key = _input_value(tree, "mbup_key")
        nonce = _input_value(tree, "nonce_rwmb-user-login")

        if mbup_key and nonce:
            resp = await self.request_with_retry(
                "POST",
                f"{BASE}/tai-khoan",
                allow_reauth=False,
                data={
                    "mbup_key": mbup_key,
                    "mbup_type": "login",
                    "nonce_rwmb-user-login": nonce,
                    "_wp_http_referer": "/tai-khoan",
                    "user_login": self.config.credentials.username,
                    "user_pass": self.config.credentials.password,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": f"{BASE}/tai-khoan"},
            )
        else:
            resp = await self.request_with_retry(
                "POST",
                f"{BASE}/wp-login.php",
                allow_reauth=False,
                data={
                    "log": self.config.credentials.username,
                    "pwd": self.config.credentials.password,
                    "wp-submit": "Log In",
                    "redirect_to": f"{BASE}/tai-khoan",
                    "testcookie": "1",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": f"{BASE}/wp-login.php"},
            )
        if resp.status_code not in (200, 302, 301):
            raise AuthError(f"Login HTTP {resp.status_code}.")

    async def _fetch_products(self, keyword: str) -> list[dict]:
        all_items: list[dict] = []
        page = 1
        while page <= _SAFETY_MAX_PAGES:
            if keyword:
                params = {"post_type": "product", "s": keyword}
                if page > 1:
                    params["paged"] = page  # search dùng query param `paged`
                resp = await self.request_with_retry("GET", f"{BASE}/", params=params)
            else:
                # Catalog (không keyword) KHÔNG dùng /shop/ (404) — dùng /product/,
                # trang sau là pretty-permalink /product/page/{n}/ (khác search).
                url = f"{BASE}/product/" if page == 1 else f"{BASE}/product/page/{page}/"
                resp = await self.request_with_retry("GET", url, params={"post_type": "product"})
            batch = self._parse_woocommerce(resp.text)
            all_items.extend(batch)
            if not batch or len(batch) < _PAGE_SIZE:
                break
            page += 1
            await self._throttle()
        else:
            self.log(f"Đạt giới hạn an toàn {_SAFETY_MAX_PAGES} trang — dừng.")
        return all_items

    @staticmethod
    def _parse_woocommerce(html: str) -> list[dict]:
        tree = HTMLParser(html)
        cards = tree.css("article.product-item")
        items: list[dict] = []
        for card in cards:
            link = card.css_first(".entry-title a") or card.css_first("a[href]")
            name = link.text(strip=True) if link else ""
            if not name:
                continue
            price_holder = card.css_first(".product-card")
            price = (price_holder.attributes.get("data-price", "0") if price_holder else "0") or "0"
            items.append({
                "name": name,
                "url": (link.attributes.get("href", "") if link else "") or "",
                "price": price,
            })
        return items

    def _parse_product(self, raw: dict) -> DrugPrice | None:
        price = parse_price(raw.get("price"))
        return DrugPrice(
            drug_name=raw.get("name", ""),
            price_vnd=price,
            price_display=format_price(price),
            source=self.source_name,
            source_url=raw.get("url", "") or BASE,
            product_id=raw.get("url", ""),
        )

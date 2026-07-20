"""ThuocSiSaiGon.vn — Haravan + ASP.NET Antiforgery, session cookie, HTML-only.

Nguồn: docs/thuocsisaigon.md + reverse-engineer trực tiếp 2026-07-11.
- Login: GET / (lấy __RequestVerificationToken) → POST /account/login
         form {form_type, utf8, __RequestVerificationToken, customer[email], customer[password]}
- Search: GET /search?q={keyword}&type=product → parse HTML (Haravan product cards).
- Catalog (không keyword): `/search?q=` (rỗng hoặc bỏ hẳn) trả 0 — endpoint search
  của Haravan không hỗ trợ "browse tất cả". Dùng `/collections/all` (quy ước chuẩn
  Haravan/Shopify cho toàn bộ sản phẩm), xác nhận sống trả đúng 32 sp/trang, phân
  trang `?page=N` hoạt động thật (page 1 ≠ page 2).
Selector giá của Haravan không cố định → thử nhiều class thường gặp.
"""

from __future__ import annotations

import httpx
from selectolax.parser import HTMLParser

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price

from ..base import AuthError, BaseCrawler

BASE = "https://thuocsisaigon.vn"
_PRICE_SELECTORS = (".price", ".product-price", ".special-price", ".money", "[class*=price]")
_NAME_SELECTORS = (".product-name", ".prod-name", ".product-title", "h3", "h2")
_SAFETY_MAX_PAGES = 200


class ThuocSiSaiGonCrawler(BaseCrawler):
    source_name = SourceName.THUOCSISAIGON

    def _is_auth_error(self, resp: httpx.Response) -> bool:
        loc = resp.headers.get("location", "")
        if resp.status_code in (301, 302, 303) and "login" in loc:
            return True
        return resp.status_code in (401, 403)

    async def _login(self) -> None:
        login_page = await self.request_with_retry("GET", f"{BASE}/account/login", allow_reauth=False)
        tree = HTMLParser(login_page.text)
        token_node = tree.css_first('input[name="__RequestVerificationToken"]')
        token = (token_node.attributes.get("value", "") if token_node else "") or ""
        if not token:
            token_node = tree.css_first('input[name="authenticity_token"]')
            token = (token_node.attributes.get("value", "") if token_node else "") or ""
        if not token:
            raise AuthError("Không tìm thấy token ở /account/login.")

        resp = await self.request_with_retry(
            "POST",
            f"{BASE}/account/login",
            allow_reauth=False,
            data={
                "form_type": "customer_login",
                "utf8": "✓",
                "__RequestVerificationToken": token,
                "customer[email]": self.config.credentials.username,
                "customer[password]": self.config.credentials.password,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": f"{BASE}/account/login"},
        )
        # Haravan: login THÀNH CÔNG luôn trả 30x redirect (về /account) + set
        # cookie phiên. Login SAI trả 200, render lại chính trang login kèm
        # node `.errors` — KHÔNG đổi status. Nếu coi 200 là OK (bản cũ), login
        # sai bị nuốt IM LẶNG → crawl như khách → mọi giá = 0 ("Đăng nhập mua
        # hàng"). Vì vậy chỉ 30x mới là thành công; 200 = thất bại, moi thông
        # báo lỗi của server để báo thẳng cho người dùng (fail loud).
        if resp.status_code in (301, 302, 303):
            return
        err_node = HTMLParser(resp.text).css_first(".errors")
        server_msg = err_node.text(strip=True) if err_node else ""
        raise AuthError(
            server_msg
            or f"Đăng nhập thất bại (HTTP {resp.status_code}) — kiểm tra lại tài khoản/mật khẩu."
        )

    async def _fetch_products(self, keyword: str) -> list[dict]:
        all_items: list[dict] = []
        page = 1
        while page <= _SAFETY_MAX_PAGES:
            if keyword:
                resp = await self.request_with_retry(
                    "GET", f"{BASE}/search", params={"q": keyword, "type": "product", "page": page}
                )
            else:
                # /search không hỗ trợ "browse tất cả" (q rỗng -> 0 kết quả) — dùng
                # /collections/all (xác nhận sống, page size = 32).
                resp = await self.request_with_retry(
                    "GET", f"{BASE}/collections/all", params={"page": page}
                )
            batch = self._parse_listing(resp.text)
            all_items.extend(batch)
            if not batch or len(batch) < 12:
                break
            page += 1
            await self._throttle()
        else:
            self.log(f"Đạt giới hạn an toàn {_SAFETY_MAX_PAGES} trang — dừng.")
        return all_items

    @staticmethod
    def _parse_listing(html: str) -> list[dict]:
        tree = HTMLParser(html)
        items: list[dict] = []
        seen: set[str] = set()
        for a in tree.css('a[href*="/products/"]'):
            href = a.attributes.get("href", "") or ""
            name = a.text(strip=True)
            if not href or not name or href in seen:
                continue
            seen.add(href)
            # Tìm giá trong card cha gần nhất.
            price_text = ""
            container = a.parent
            for _ in range(3):  # leo tối đa 3 cấp tìm block chứa giá
                if container is None:
                    break
                for sel in _PRICE_SELECTORS:
                    node = container.css_first(sel)
                    if node and any(ch.isdigit() for ch in node.text()):
                        price_text = node.text(strip=True)
                        break
                if price_text:
                    break
                container = container.parent
            items.append({"name": name, "url": href, "price": price_text})
        return items

    def _parse_product(self, raw: dict) -> DrugPrice | None:
        price = parse_price(raw.get("price"))
        url = raw.get("url", "")
        if url and url.startswith("/"):
            url = BASE + url
        return DrugPrice(
            drug_name=raw.get("name", ""),
            price_vnd=price,
            price_display=format_price(price),
            source=self.source_name,
            source_url=url or BASE,
            product_id=raw.get("url", ""),
        )

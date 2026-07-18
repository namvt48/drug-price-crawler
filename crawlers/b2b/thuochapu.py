"""ThuocHaPu.com — Joomla, session cookie + security token, HTML-only.

Nguồn: docs/thuochapu.md.
- Login: GET /login.html (lấy Joomla token = hidden input tên hash 32-hex, value=1)
         → POST /login.html?task=user.login form {username, password, return, <hash>=1}
- Search: GET /search.html?filter_search={keyword} → parse HTML (không phân trang).
Giá dạng "48.000" (dấu chấm ngăn nghìn). Link sản phẩm: /thuoc/{slug}.html.
"""

from __future__ import annotations

import re

import httpx
from selectolax.parser import HTMLParser

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price

from ..base import AuthError, BaseCrawler

BASE = "https://thuochapu.com"
_HASH_NAME = re.compile(r"^[a-f0-9]{32}$")
_PRICE_TEXT = re.compile(r"^[\d.]{3,}$")


class ThuocHaPuCrawler(BaseCrawler):
    source_name = SourceName.THUOCHAPU

    def _is_auth_error(self, resp: httpx.Response) -> bool:
        loc = resp.headers.get("location", "")
        if resp.status_code in (301, 302, 303) and "login" in loc:
            return True
        return resp.status_code in (401, 403)

    async def _login(self) -> None:
        page = await self.request_with_retry("GET", f"{BASE}/login.html", allow_reauth=False)
        token_name = self._extract_joomla_token(page.text)
        if not token_name:
            raise AuthError("Không tìm thấy Joomla security token ở trang login.")

        resp = await self.request_with_retry(
            "POST",
            f"{BASE}/login.html?task=user.login",
            allow_reauth=False,
            data={
                "username": self.config.credentials.username,
                "password": self.config.credentials.password,
                "return": "",
                token_name: "1",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "Referer": f"{BASE}/login.html"},
        )
        if resp.status_code not in (200, 302, 303):
            raise AuthError(f"Login HTTP {resp.status_code}.")

    @staticmethod
    def _extract_joomla_token(html: str) -> str:
        tree = HTMLParser(html)
        for inp in tree.css('input[type="hidden"]'):
            name = inp.attributes.get("name", "") or ""
            value = inp.attributes.get("value", "") or ""
            if value == "1" and _HASH_NAME.match(name):
                return name
        return ""

    async def _fetch_products(self, keyword: str) -> list[dict]:
        if keyword:
            # Search trả tất cả kết quả trong 1 trang — không phân trang.
            resp = await self.request_with_retry(
                "GET",
                f"{BASE}/search.html",
                params={"filter_search": keyword},
                headers={"Referer": f"{BASE}/danh-muc.html"},
            )
            return self._parse_listing(resp.text)

        # Crawl ALL: phân trang qua /danh-muc.html?start=N (60 sản phẩm/trang).
        all_items: list[dict] = []
        start = 0
        while True:
            resp = await self.request_with_retry(
                "GET",
                f"{BASE}/danh-muc.html",
                params={"start": start},
                headers={"Referer": f"{BASE}/danh-muc.html"},
            )
            batch = self._parse_listing(resp.text)
            all_items.extend(batch)
            if len(batch) < 60:
                break
            start += 60
            await self._throttle()
        return all_items

    @staticmethod
    def _parse_listing(html: str) -> list[dict]:
        """Ghép link sản phẩm (/thuoc/*.html) với giá theo thứ tự tài liệu.

        Cấu trúc thuochapu: <a> tên và <b> giá nằm ở div khác nhau nên không
        cùng parent. Cách bền nhất khi không có DOM đầy đủ: gom link + gom giá
        theo document-order rồi zip 1-1.
        """
        tree = HTMLParser(html)

        seen: set[str] = set()
        links: list[dict] = []
        for a in tree.css('a[href*="/thuoc/"]'):
            href = a.attributes.get("href", "") or ""
            name = a.text(strip=True)
            if href and name and href not in seen:
                seen.add(href)
                img = a.css_first("img")
                img_src = (img.attributes.get("src", "") or "") if img else ""
                links.append({"name": name, "url": href, "image": img_src})

        price_nodes = [
            b.text(strip=True) for b in tree.css("b") if _PRICE_TEXT.match(b.text(strip=True))
        ]

        items: list[dict] = []
        for i, link in enumerate(links):
            price = price_nodes[i] if i < len(price_nodes) else ""
            items.append({**link, "price": price})
        return items

    def _parse_product(self, raw: dict) -> DrugPrice | None:
        price = parse_price(raw.get("price"))
        url = raw.get("url", "") or ""
        image = raw.get("image", "") or ""
        image_url = image if image.startswith("http") else f"{BASE}/{image.lstrip('/')}" if image else ""
        return DrugPrice(
            drug_name=raw.get("name", ""),
            price_vnd=price,
            price_display=format_price(price),
            source=self.source_name,
            source_url=url or BASE,
            product_id=url,
            image_url=image_url,
        )

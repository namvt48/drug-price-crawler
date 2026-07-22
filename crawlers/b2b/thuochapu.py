"""ThuocHaPu.com — Joomla, session cookie + security token, HTML-only.

Nguồn: docs/thuochapu.md.
- Login: GET /login.html (lấy Joomla token = hidden input tên hash 32-hex, value=1)
         → POST /login.html?task=user.login form {username, password, return, <hash>=1}
- Search: GET /search.html?filter_search={keyword} → parse HTML (không phân trang).
Giá dạng "48.000" (dấu chấm ngăn nghìn). Link sản phẩm: /thuoc/{slug}.html.
"""

from __future__ import annotations

import json
import re

import httpx
from selectolax.parser import HTMLParser

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price
from utils.stock_status import detect_stock_status

from ..base import AuthError, BaseCrawler

BASE = "https://thuochapu.com"
_HASH_NAME = re.compile(r"^[a-f0-9]{32}$")
_PRICE_TEXT = re.compile(r"^[\d.]{3,}$")


class ThuocHaPuCrawler(BaseCrawler):
    source_name = SourceName.THUOCHAPU
    direct_fetch_supported = True
    # search.html BỎ QUA `filter_search` — luôn trả nguyên trang đầu catalog dù
    # tìm gì (xác nhận sống 2026-07-20). Vì vậy KHÔNG dùng keyword search để tra
    # giá theo tên: luồng CLI crawl toàn catalog rồi lọc tại chỗ; luồng GUI (chọn
    # 1 sản phẩm) gọi fetch_price_by_id() đọc giá từ trang chi tiết. Nếu để True,
    # GUI search "Alaxan" nhận nhầm trang đầu (3B...) → mọi sản phẩm hiện chung
    # 1 giá (48.000 của sản phẩm đầu).
    keyword_search_supported = False

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
        """Ghép link sản phẩm (/thuoc/*.html) với giá THEO TỪNG CARD.

        Mỗi sản phẩm nằm trong 1 card `div.t3-medicine` chứa đúng 1 link
        `/thuoc/` và 1 `<b>` giá. Trước đây code gom toàn bộ link + toàn bộ
        `<b>` rồi zip 1-1 theo document-order; nhưng trang còn có `<b>` rác
        (vd bộ đếm tổng sản phẩm "2646" ở header) khớp regex giá → lọt vào
        đầu danh sách và đẩy LỆCH toàn bộ giá đi 1 ô (mọi sản phẩm nhận nhầm
        giá của sản phẩm khác). Duyệt theo card `.t3-medicine` loại bỏ hẳn
        rủi ro này vì `<b>` rác nằm ngoài mọi card.
        """
        tree = HTMLParser(html)

        seen: set[str] = set()
        items: list[dict] = []
        for card in tree.css("div.t3-medicine"):
            anchors = card.css('a[href*="/thuoc/"]')
            if not anchors:
                continue
            href = anchors[0].attributes.get("href", "") or ""
            name = next((t for a in anchors if (t := a.text(strip=True))), "")
            if not href or not name or href in seen:
                continue
            seen.add(href)

            price = next(
                (t for b in card.css("b") if _PRICE_TEXT.match(t := b.text(strip=True))),
                "",
            )
            img = card.css_first("img")
            img_src = (img.attributes.get("src", "") or "") if img else ""
            out_node = card.css_first(".out-of-stock")
            stock_node = out_node or card.css_first(".stock")
            items.append(
                {
                    "name": name,
                    "url": href,
                    "image": img_src,
                    "price": price,
                    "stock_status": (
                        "out_of_stock"
                        if out_node is not None
                        else stock_node.text(strip=True) if stock_node else ""
                    ),
                }
            )
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
            stock_status=detect_stock_status(raw),
            source=self.source_name,
            source_url=url or BASE,
            product_id=url,
            image_url=image_url,
        )

    async def fetch_price_by_id(self, product_id: str) -> DrugPrice | None:
        """Giá LIVE cho đúng 1 sản phẩm — GET trang chi tiết, đọc JSON-LD
        schema.org/Offer. Dùng cho luồng GUI (chọn 1 sản phẩm): search.html BỎ
        QUA keyword nên không tra giá theo tên được; trang chi tiết mới là nguồn
        giá chính xác từng SKU (vd Alaxan = 110.000, không phải 48.000 của trang
        đầu). `product_id` = URL trang chi tiết (catalog lưu sẵn full URL)."""
        if not product_id:
            return None
        url = product_id if product_id.startswith("http") else f"{BASE}/{product_id.lstrip('/')}"
        await self.ensure_auth()
        resp = await self.request_with_retry("GET", url)
        return self._parse_detail(resp.text, url)

    def _parse_detail(self, html: str, url: str) -> DrugPrice | None:
        tree = HTMLParser(html)
        for script in tree.css('script[type="application/ld+json"]'):
            try:
                # strict=False: JSON-LD của thuochapu có ký tự xuống dòng THÔ
                # trong `description` (control char) — json.loads mặc định ném
                # lỗi, strict=False cho phép.
                data = json.loads(script.text() or "", strict=False)
            except (ValueError, TypeError):
                continue
            for entry in data if isinstance(data, list) else [data]:
                if not isinstance(entry, dict) or entry.get("@type") != "Product":
                    continue
                offers = entry.get("offers") or {}
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                price = parse_price(offers.get("price") if isinstance(offers, dict) else 0)
                image = entry.get("image") or ""
                if isinstance(image, list):
                    image = image[0] if image else ""
                return DrugPrice(
                    drug_name=entry.get("name", "") or "",
                    price_vnd=price,
                    price_display=format_price(price),
                    stock_status=detect_stock_status(
                        offers if isinstance(offers, dict) else None
                    ),
                    source=self.source_name,
                    source_url=url,
                    product_id=url,
                    image_url=image if isinstance(image, str) else "",
                )
        return None

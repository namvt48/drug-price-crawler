"""ThuocTot3Mien.vn — Next.js + Laravel API, auth Bearer token.

Nguồn: reverse-engineer trực tiếp 2026-07-11 (docs cũ đúng field/endpoint nhưng
THIẾU 1 điều kiện quan trọng — xem ghi chú dưới).
- Login:  POST api/web/v1/customer/login {email, password}  (email = SĐT!)
- Search: GET  api/web/v1/products?page&limit&search  (Bearer)

QUAN TRỌNG: backend đòi hỏi header `Origin`/`Referer` khớp domain thật
(`https://thuoctot3mien.vn`) — thiếu 2 header này, login trả 401 "Tài khoản hoặc
mật khẩu không chính xác" DÙ MẬT KHẨU ĐÚNG (thông báo lỗi cố tình mơ hồ, không lộ
lý do thật — kỹ thuật chống bot phổ biến). Xác nhận sống: cùng 1 request y hệt,
chỉ thêm 2 header này → login OK. Token nằm ở `data.token` (không phải
`data.accessToken` như docs cũ ghi).
"""

from __future__ import annotations

from typing import Any

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price
from utils.stock_status import detect_stock_status

from ..base import AuthError, BaseCrawler

API = "https://api.thuoctot3mien.vn/api/web/v1"
_LIMIT = 20
# Chặn cứng an toàn — không dùng để giới hạn data thật (vòng lặp dừng đúng lúc dựa
# vào `meta.total`, xem _fetch_products).
_SAFETY_MAX_PAGES = 1000
# Backend chặn request thiếu Origin/Referer hợp lệ bằng lỗi 401 sai-mật-khẩu mơ hồ
# (xác nhận sống 2026-07-11) — bắt buộc gửi 2 header này trên MỌI request.
_ORIGIN_HEADERS = {
    "Origin": "https://thuoctot3mien.vn",
    "Referer": "https://thuoctot3mien.vn/dang-nhap",
}


def _first(raw: dict, keys: tuple[str, ...], default: Any = "") -> Any:
    for k in keys:
        if raw.get(k):
            return raw[k]
    return default


def _first_str(raw: dict, keys: tuple[str, ...], default: str = "") -> str:
    """Như `_first` nhưng bỏ qua giá trị không phải string — API thật có field
    trùng tên (vd "unit") lại trả object quan hệ lồng nhau (vd {"id":2,"uuid":...})
    thay vì chuỗi, dùng cho name/manufacturer/dosage_form (xác nhận sống 2026-07-11)."""
    for k in keys:
        v = raw.get(k)
        if isinstance(v, str) and v:
            return v
    return default


class ThuocTot3MienCrawler(BaseCrawler):
    source_name = SourceName.THUOCTOT3MIEN
    direct_fetch_supported = True

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            **_ORIGIN_HEADERS,
        }

    async def _login(self) -> None:
        resp = await self.request_with_retry(
            "POST",
            f"{API}/customer/login",
            allow_reauth=False,
            json={
                "email": self.config.credentials.username,  # doc: field 'email' nhận SĐT
                "password": self.config.credentials.password,
            },
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                **_ORIGIN_HEADERS,
            },
        )
        if resp.status_code != 200:
            raise AuthError(f"Login HTTP {resp.status_code}: {resp.text[:150]}")
        data = (resp.json() or {}).get("data") or {}
        self._token = data.get("token") or data.get("accessToken") or data.get("access_token") or ""
        if not self._token:
            raise AuthError("Không lấy được token đăng nhập.")

    async def _fetch_products(self, keyword: str) -> list[dict]:
        products: list[dict] = []
        page = 1
        total: int | None = None
        for _ in range(_SAFETY_MAX_PAGES):
            params: dict[str, Any] = {"page": page, "limit": _LIMIT}
            if keyword:
                # Gửi `search=""` (rỗng) khi không có keyword khiến server trả 0
                # sản phẩm thay vì "không lọc" — xác nhận sống 2026-07-11. Chỉ gửi
                # field này khi thật sự có từ khóa.
                params["search"] = keyword
            resp = await self.request_with_retry(
                "GET",
                f"{API}/products",
                params=params,
                headers=self._headers(),
            )
            body = resp.json() or {}
            data = body.get("data")
            batch = data.get("data") if isinstance(data, dict) else data
            batch = batch or []
            meta = data.get("meta") if isinstance(data, dict) else None
            if total is None and isinstance(meta, dict) and isinstance(meta.get("total"), int):
                total = meta["total"]
            products.extend(batch)
            # `per_page` THẬT của server = 15, bất kể `limit` gửi lên là bao nhiêu
            # (xác nhận sống 2026-07-11) — `len(batch) < _LIMIT (20)` luôn đúng dù
            # còn hàng trăm trang, khiến vòng lặp dừng ngay sau trang 1 (bug thật
            # đã gặp: catalog chỉ lấy được 15/4.672 sản phẩm). Phải dùng
            # `meta.total` để biết chính xác khi nào dừng.
            if not batch or (total is not None and len(products) >= total):
                break
            if total is None and len(batch) < _LIMIT:
                break  # fallback nếu response thiếu meta.total
            page += 1
            await self._throttle()
        else:
            self.log(f"Đạt giới hạn an toàn {_SAFETY_MAX_PAGES} trang — dừng.")
        return products

    def _parse_product(self, raw: dict) -> DrugPrice | None:
        name = _first_str(raw, ("name", "product_name", "title"))
        # `price` thật là OBJECT lồng {"base":65000,"final":65000,...} (final = giá
        # sau flash-sale/giảm giá nếu có) — không phải số. Ưu tiên price.final, rồi
        # price.base, rồi các field chuỗi khác (xác nhận sống 2026-07-11).
        price_obj = raw.get("price")
        if isinstance(price_obj, dict):
            raw_price = price_obj.get("final") or price_obj.get("base") or 0
        else:
            raw_price = _first(
                raw, ("sale_price", "wholesale_price", "base_price", "selling_price"), 0
            )
        # `base_price` thật trả chuỗi thập phân kiểu "65000.00" (xác nhận sống
        # 2026-07-11) — parse_price() lấy toàn bộ chữ số nên "65000.00" -> 6500000
        # (nhân nhầm 100 lần). Ép về float trước để bỏ đúng phần thập phân.
        try:
            raw_price = float(raw_price)
        except (TypeError, ValueError):
            pass
        price = parse_price(raw_price)
        slug = _first(raw, ("slug", "id"))
        return DrugPrice(
            drug_name=name,
            manufacturer=_first_str(raw, ("manufacturer", "producer", "brand")),
            dosage_form=_first_str(raw, ("packaging", "unit")),
            price_vnd=price,
            price_display=format_price(price),
            stock_status=detect_stock_status(raw),
            source=self.source_name,
            source_url=f"{self.config.base_url}/san-pham/{slug}" if slug else self.config.base_url,
            product_id=str(slug) if slug else "",
        )

    async def fetch_price_by_id(self, product_id: str) -> DrugPrice | None:
        """Lấy đúng một sản phẩm qua endpoint `/products/{id}`."""
        if not product_id:
            return None
        await self.ensure_auth()
        resp = await self.request_with_retry(
            "GET",
            f"{API}/products/{product_id}",
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return None
        body = resp.json() or {}
        raw = body.get("data") if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            return None
        return self._parse_product(raw)

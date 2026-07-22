"""ThuocSi.vn — Next.js + Buymed backend, Basic Auth + Bearer token.

Nguồn: reverse-engineer trực tiếp từ JS bundle production 2026-07-11 (docs/thuocsi.md
cũ ghi sai endpoint — không dùng nữa, xem ghi chú dưới).
- Login: POST backend/marketplace/customer/v1/authentication
         {username, password, type: "CUSTOMER", deviceId} (Basic Auth)
         — KHÔNG phải {phone,...} lên "/login" như bản cũ (luôn trả 401 "Wrong
         password" dù mật khẩu đúng, vì sai field + sai path).
- List:  POST backend/marketplace/frontend-apis/v2/screen/product/list
         {offset, limit, text, isAvailable, queryOption} (Bearer)
         — KHÔNG phải "backend/screen/product/list" (404, thiếu tiền tố
         marketplace/frontend-apis/v2).
- Giá:   Response KHÔNG trả giá thô — trả `priceEncrypted`/`discountPriceEncrypted`
         (base64, AES-CBC, key=IV suy từ chuỗi bí mật cố định trong JS bundle:
         "thu0c21.v4@2023?buym3d"). Xem `_derive_key`/`_decrypt_price`.
Basic Auth credentials cố định cho endpoint isBasic=true.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

from Crypto.Cipher import AES

from utils.models import DrugPrice, SourceName
from utils.price_parser import format_price, parse_price
from utils.stock_status import detect_stock_status

from ..base import AuthError, BaseCrawler

BASE = "https://thuocsi.vn/backend"
BASIC = "UEFSVE5FUi92Mi5mcm9udGVuZC53ZWI6Nk11d1ZUazRRd1VkZXdoUA=="
_PAGE = 20
# Chặn cứng an toàn — không dùng để giới hạn data thật (xem _fetch_products: vòng
# lặp dừng đúng lúc dựa vào field `total` của response).
_SAFETY_MAX_PAGES = 1200
# Chuỗi bí mật hardcode trong JS bundle production (module export `YU`) — dùng để
# suy khoá AES giải mã giá. Xác nhận sống 2026-07-11: giải mã đúng ra số giá thật,
# đối chiếu khớp discountPercent trong cùng response.
_PRICE_KEY_SEED = "thu0c21.v4@2023?buym3d"


def _derive_key(seed: str) -> bytes:
    """Y hệt hàm `R(e)` trong JS bundle: cộng dồn charCode<<10 của từng ký tự
    thành 1 số nguyên lớn, lấy chuỗi thập phân của số đó làm bytes, pad/cắt về
    đúng 16 byte (pad bằng 127, cắt lấy 16 byte đầu nếu dư)."""
    n = 0
    for ch in seed:
        n += ord(ch) << 10
    digits = str(n).encode("ascii")
    if len(digits) < 16:
        return digits + bytes([127]) * (16 - len(digits))
    return digits[:16]


def _decrypt_price(value: str | None) -> int:
    """Y hệt hàm `A(e)` trong JS bundle: AES-CBC với key=IV (cùng 1 giá trị),
    plaintext là số giá dạng chuỗi, đệm bằng khoảng trắng cho đủ block 16 byte."""
    if not value:
        return 0
    try:
        key = _derive_key(_PRICE_KEY_SEED)
        ciphertext = base64.b64decode(value)
        plain = AES.new(key, AES.MODE_CBC, iv=key).decrypt(ciphertext)
        return parse_price(plain.decode("utf-8", errors="ignore").strip())
    except Exception:
        return 0


def _walk_products(body: Any) -> list[dict]:
    """Tìm list sản phẩm trong response Buymed (data có thể là list hoặc dict)."""
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("products", "list", "items", "productList"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _total_of(body: Any) -> int | None:
    """Field `total` của response — xác nhận sống 2026-07-11: 1 trang giữa catalog
    có thể trả THIẾU so với `limit` (vd 19/20) dù còn rất nhiều dữ liệu phía sau —
    không thể dùng `len(batch) < limit` để biết đã hết trang, phải dựa vào `total`."""
    total = body.get("total") if isinstance(body, dict) else None
    return total if isinstance(total, int) else None


class ThuocSiCrawler(BaseCrawler):
    source_name = SourceName.THUOCSI
    # GUI lưu slug lấy trực tiếp từ URL sản phẩm. ThuocSi hiện có endpoint chi
    # tiết ổn định theo slug, vì vậy không được fallback sang tìm bằng tên.
    direct_fetch_supported = True

    def _headers(self, bearer: bool = True) -> dict[str, str]:
        h: dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json"}
        if bearer and self._token:
            h["Authorization"] = f"Bearer {self._token}"
        else:
            h["Authorization"] = f"Basic {BASIC}"
        return h

    async def _login(self) -> None:
        resp = await self.request_with_retry(
            "POST",
            f"{BASE}/marketplace/customer/v1/authentication",
            allow_reauth=False,
            json={
                "username": self.config.credentials.username,
                "password": self.config.credentials.password,
                "type": "CUSTOMER",
                "deviceId": str(uuid.uuid4()),
            },
            headers=self._headers(bearer=False),
        )
        if resp.status_code != 200:
            raise AuthError(f"Login HTTP {resp.status_code}: {resp.text[:150]}")
        self._token = self._extract_token(resp.json() or {})
        if not self._token:
            raise AuthError("Không lấy được token đăng nhập.")

    @staticmethod
    def _extract_token(body: dict) -> str:
        data = body.get("data")
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict):
                for key in ("bearerToken", "token", "accessToken", "jwt", "access_token"):
                    if item.get(key):
                        return str(item[key])
        return ""

    async def _fetch_products(self, keyword: str) -> list[dict]:
        products: list[dict] = []
        offset = 0
        total: int | None = None
        for _ in range(_SAFETY_MAX_PAGES):
            body: dict[str, Any] = {
                "offset": offset,
                "limit": _PAGE,
                "filter": {},
                "isAvailable": True,
                "queryOption": {},
            }
            if keyword:
                body["text"] = keyword
            resp = await self.request_with_retry(
                "POST",
                f"{BASE}/marketplace/frontend-apis/v2/screen/product/list",
                json=body,
                headers=self._headers(),
            )
            resp_body = resp.json() or {}
            batch = _walk_products(resp_body)
            if total is None:
                total = _total_of(resp_body)
            products.extend(batch)
            offset += _PAGE
            if not batch or (total is not None and offset >= total):
                break
            if total is None and len(batch) < _PAGE:
                break  # fallback nếu response thiếu field `total`
            await self._throttle()
        else:
            self.log(f"Đạt giới hạn an toàn {_SAFETY_MAX_PAGES} trang — dừng.")
        return products

    def _parse_product(self, raw: dict) -> DrugPrice | None:
        # Endpoint danh sách cũ trả field phẳng; endpoint chi tiết hiện hành trả
        # `{product: {...}, sku: {...}, isAvailable: ...}`. Hỗ trợ cả hai để
        # không làm hỏng luồng refresh catalog/test fixture cũ.
        product = raw.get("product") if isinstance(raw.get("product"), dict) else {}
        sku = raw.get("sku") if isinstance(raw.get("sku"), dict) else {}
        name = raw.get("productName") or raw.get("name") or product.get("name") or ""
        # Giá KHÔNG trả thô — trả priceEncrypted/discountPriceEncrypted (AES-CBC,
        # xem _decrypt_price). Ưu tiên discountPriceEncrypted (giá bán thật sau
        # khuyến mãi), fallback priceEncrypted (giá gốc) nếu sản phẩm không giảm giá.
        price = (
            _decrypt_price(raw.get("discountPriceEncrypted"))
            or _decrypt_price(sku.get("retailPriceApplyVoucherEncrypt"))
            or _decrypt_price(raw.get("priceEncrypted"))
            or _decrypt_price(sku.get("retailPriceValueEncrypt"))
            or parse_price(sku.get("retailPriceValue") or 0)
        )
        slug = (
            raw.get("slug")
            or raw.get("skuCode")
            or raw.get("code")
            or sku.get("slug")
            or sku.get("code")
            or ""
        )
        return DrugPrice(
            drug_name=name,
            dosage_form=(
                raw.get("volume")
                or raw.get("unit")
                or product.get("volume")
                or product.get("unit")
                or ""
            ),
            price_vnd=price,
            price_display=format_price(price),
            stock_status=detect_stock_status(raw),
            source=self.source_name,
            source_url=(
                f"{self.config.base_url}/product/{slug}"
                if slug
                else self.config.base_url
            ),
            product_id=str(slug),
        )

    async def fetch_price_by_id(self, product_id: str) -> DrugPrice | None:
        """Lấy đúng một sản phẩm qua endpoint chi tiết theo slug.

        Không dùng endpoint danh sách `/screen/product/list`: endpoint đó đã
        trả 404 từ 2026-07-22 và trước đây còn ép `isAvailable=true`, khiến SKU
        hết hàng biến mất rồi bị GUI hiểu nhầm là lỗi giá.
        """
        slug = product_id.strip().lower()
        if not slug:
            return None
        await self.ensure_auth()
        resp = await self.request_with_retry(
            "GET",
            f"{BASE}/marketplace/frontend-apis/v2/product/detail-encrypted",
            params={"q": slug, "queryOption": "isReplacePriceAfterVoucher"},
            headers=self._headers(),
        )
        if resp.status_code != 200:
            return None
        for raw in _walk_products(resp.json() or {}):
            result = self._parse_product(raw)
            if result is not None and result.product_id.lower() == slug:
                return result
        return None

"""Xác thực URL sản phẩm người dùng dán và tách ``product_id`` tương ứng.

Dùng chung cho cả Thêm sản phẩm mới và Sửa link. URL phải thuộc đúng domain và
đúng path trang chi tiết của site; chuỗi ID/path do ứng dụng tự tách, người dùng
không bao giờ phải nhập ``product_id`` riêng.

Ví von: mỗi site giấu "số hồ sơ" (product_id) ở một chỗ khác nhau trong URL — có
site để lộ hẳn ra (số hoặc slug ngay trên URL), có site dùng chính URL làm số hồ sơ
luôn. Module này chỉ là "tra đúng chỗ" theo quy tắc của từng site, y hệt cách mỗi
`crawlers/b2b/<site>.py::_parse_product` tự dựng `product_id` — để sản phẩm thêm tay
khớp được với kết quả tìm kiếm thật sau này (`CrawlerEngine.fetch_live_prices` lọc
theo đúng `product_id`).

Quy tắc từng site (đã xác minh khớp dữ liệu thật trong
output/catalog_master.xlsx):
- bachhoathuoc: SKU nằm ở cuối URL dạng "...--s<số>".
- chothuoc247: số ID nằm ở path "/san-pham/<số>" hoặc cuối slug sản phẩm
  dạng "/san-pham/<slug>-<số>.html".
- chothuoctot: số ID nằm ở query param "?id=<số>" hoặc đầu path mới
  "/san-pham/<số>-<slug>".
- giathuoctot: slug nằm ở path "/product/<slug>".
- thuoctot3mien: ID nằm ở "/san-pham/<id>" hoặc cuối URL public
  "/<slug>-p<id>.html".
- thuocsi: slug nằm ở "/<slug>" hoặc path public mới "/product/<slug>".
- duocphamgiasi, thuochapu: product_id = CHÍNH URL (các crawler này dùng thẳng URL
  gốc làm product_id).
- thuocsisaigon: product_id = PATH TƯƠNG ĐỐI (không kèm domain) — khác các site trên,
  vì `_parse_product` của site này giữ nguyên href gốc (relative) làm product_id,
  chỉ ghép domain riêng cho `source_url`.
"""

from __future__ import annotations

import re
from urllib.parse import SplitResult, parse_qs, urlsplit

_SITE_DOMAINS = {
    "bachhoathuoc": "bachhoathuoc.com",
    "chothuoc247": "chothuoc247.vn",
    "chothuoctot": "chothuoctot.vn",
    "duocphamgiasi": "duocphamgiasi.vn",
    "giathuoctot": "giathuoctot.com",
    "thuochapu": "thuochapu.com",
    "thuocsi": "thuocsi.vn",
    "thuocsisaigon": "thuocsisaigon.vn",
    "thuoctot3mien": "thuoctot3mien.vn",
}

_BACHHOATHUOC_RE = re.compile(r"--s(\d+)/?$")
_CHOTHUOC247_RE = re.compile(
    r"^/san-pham/(?:[^/]+-)?(\d+)(?:\.html)?/?$", re.IGNORECASE
)
_CHOTHUOCTOT_RE = re.compile(
    r"^/san-pham/(\d+)(?:-[^/]+)?/?$", re.IGNORECASE
)
_DUOCPHAMGIASI_RE = re.compile(r"^/product/[^/]+/?$", re.IGNORECASE)
_GIATHUOCTOT_RE = re.compile(r"^/product/([^/]+)/?$", re.IGNORECASE)
_THUOCHAPU_RE = re.compile(r"^/thuoc/[^/]+\.html/?$", re.IGNORECASE)
_THUOCSI_RE = re.compile(r"^/(?:product/)?([^/]+)/?$", re.IGNORECASE)
_THUOCSISAIGON_RE = re.compile(r"^/products/[^/]+/?$", re.IGNORECASE)
_THUOCTOT3MIEN_RE = re.compile(r"^/san-pham/([^/]+)/?$", re.IGNORECASE)
_THUOCTOT3MIEN_PUBLIC_RE = re.compile(
    r"^/[^/]+-p(\d+)\.html/?$", re.IGNORECASE
)

# Segment "trang trí" thuần số/rỗng không dùng làm gợi ý tên sản phẩm.
_NUMERIC_RE = re.compile(r"^\d+$")


def _parse_site_url(site_id: str, url: str) -> SplitResult | None:
    """Chỉ nhận URL HTTP(S) thuộc domain của đúng site đang nhập."""
    expected_domain = _SITE_DOMAINS.get(site_id)
    if expected_domain is None:
        return None
    parts = urlsplit(url)
    hostname = (parts.hostname or "").rstrip(".").lower()
    if parts.scheme.lower() not in ("http", "https") or not hostname:
        return None
    if hostname != expected_domain and not hostname.endswith(f".{expected_domain}"):
        return None
    return parts


def detect_product_id(site_id: str, url: str) -> str | None:
    """Tách product_id từ URL theo đúng quy tắc site đó dùng thật (xem bảng ở
    module docstring). Không khớp được (URL sai định dạng/thiếu phần cần thiết) →
    trả None — gọi nơi dùng tự bỏ qua site này, không chặn các site khác."""
    url = (url or "").strip()
    if not url:
        return None

    parts = _parse_site_url(site_id, url)
    if parts is None:
        return None

    if site_id == "bachhoathuoc":
        m = _BACHHOATHUOC_RE.search(parts.path)
        return m.group(1) if m else None

    if site_id == "chothuoc247":
        # URL public có cả dạng ID thuần và slug mô tả. Chỉ lấy cụm số ở CUỐI
        # segment để không nhầm các số quy cách như ``10v`` trong tên sản phẩm.
        m = _CHOTHUOC247_RE.match(parts.path)
        return m.group(1) if m else None

    if site_id == "chothuoctot":
        path_match = _CHOTHUOCTOT_RE.match(parts.path)
        if path_match:
            return path_match.group(1)
        if parts.path.rstrip("/") == "/san-pham":
            ids = parse_qs(parts.query).get("id")
            return ids[0] if ids and ids[0].isdigit() else None
        return None

    if site_id == "duocphamgiasi":
        return url if _DUOCPHAMGIASI_RE.match(parts.path) else None

    if site_id == "giathuoctot":
        m = _GIATHUOCTOT_RE.match(parts.path)
        return m.group(1) if m else None

    if site_id == "thuochapu":
        return url if _THUOCHAPU_RE.match(parts.path) else None

    if site_id == "thuoctot3mien":
        m = _THUOCTOT3MIEN_RE.match(parts.path)
        if m:
            return m.group(1)
        public_match = _THUOCTOT3MIEN_PUBLIC_RE.match(parts.path)
        return public_match.group(1) if public_match else None

    if site_id == "thuocsi":
        m = _THUOCSI_RE.match(parts.path)
        if not m or parts.path.rstrip("/").casefold() == "/product":
            return None
        return m.group(1)

    if site_id == "thuocsisaigon":
        if not _THUOCSISAIGON_RE.match(parts.path):
            return None
        return parts.path + (f"?{parts.query}" if parts.query else "")

    return None


def suggest_name_from_urls(urls: list[str]) -> str:
    """Gợi ý 1 tên sản phẩm bằng cách lấy segment path DÀI NHẤT (nhiều chữ, không
    phải số thuần) trong toàn bộ URL đã dán, de-slugify (-/_ → khoảng trắng, bỏ đuôi
    file/query). Chỉ là GỢI Ý — người dùng luôn sửa lại ở bước xác nhận, không phải
    auto-detect chính xác."""
    best = ""
    for url in urls:
        if not url or not url.strip():
            continue
        path = urlsplit(url.strip()).path
        for segment in path.split("/"):
            segment = segment.rsplit(".", 1)[0]  # bỏ đuôi file (vd .html)
            if not segment or _NUMERIC_RE.match(segment):
                continue
            if len(segment) > len(best):
                best = segment

    if not best:
        return ""
    words = re.split(r"[-_]+", best)
    return " ".join(w for w in words if w).strip()

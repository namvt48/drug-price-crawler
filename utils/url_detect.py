"""Tách `product_id` từ 1 URL sản phẩm dán tay (tính năng 'Thêm sản phẩm mới' trong
GUI) — CƠ HỌC, không gọi mạng, không phụ thuộc crawler.

Ví von: mỗi site giấu "số hồ sơ" (product_id) ở một chỗ khác nhau trong URL — có
site để lộ hẳn ra (số hoặc slug ngay trên URL), có site dùng chính URL làm số hồ sơ
luôn. Module này chỉ là "tra đúng chỗ" theo quy tắc của từng site, y hệt cách mỗi
`crawlers/b2b/<site>.py::_parse_product` tự dựng `product_id` — để sản phẩm thêm tay
khớp được với kết quả tìm kiếm thật sau này (`CrawlerEngine.fetch_live_prices` lọc
theo đúng `product_id`).

Quy tắc từng site (đã xác minh khớp dữ liệu thật trong
output/catalog_master_entity_resolved.xlsx):
- bachhoathuoc: SKU nằm ở cuối URL dạng "...--s<số>".
- chothuoc247: số ID nằm ở path "/san-pham/<số>".
- chothuoctot: số ID nằm ở query param "?id=<số>".
- giathuoctot, thuoctot3mien: slug nằm ở path "/product/<slug>" / "/san-pham/<slug>".
- thuocsi: slug là segment cuối path (ngay dưới domain).
- duocphamgiasi, thuochapu: product_id = CHÍNH URL (các crawler này dùng thẳng URL
  gốc làm product_id).
- thuocsisaigon: product_id = PATH TƯƠNG ĐỐI (không kèm domain) — khác các site trên,
  vì `_parse_product` của site này giữ nguyên href gốc (relative) làm product_id,
  chỉ ghép domain riêng cho `source_url`.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

_BACHHOATHUOC_RE = re.compile(r"--s(\d+)/?$")
_CHOTHUOC247_RE = re.compile(r"/san-pham/(\d+)")
_GIATHUOCTOT_RE = re.compile(r"/product/([^/?#]+)")
_THUOCTOT3MIEN_RE = re.compile(r"/san-pham/([^/?#]+)")

# Segment "trang trí" thuần số/rỗng không dùng làm gợi ý tên sản phẩm.
_NUMERIC_RE = re.compile(r"^\d+$")


def detect_product_id(site_id: str, url: str) -> str | None:
    """Tách product_id từ URL theo đúng quy tắc site đó dùng thật (xem bảng ở
    module docstring). Không khớp được (URL sai định dạng/thiếu phần cần thiết) →
    trả None — gọi nơi dùng tự bỏ qua site này, không chặn các site khác."""
    url = url.strip()
    if not url:
        return None

    if site_id == "bachhoathuoc":
        m = _BACHHOATHUOC_RE.search(url)
        return m.group(1) if m else None

    if site_id == "chothuoc247":
        m = _CHOTHUOC247_RE.search(url)
        return m.group(1) if m else None

    if site_id == "chothuoctot":
        qs = parse_qs(urlsplit(url).query)
        ids = qs.get("id")
        return ids[0] if ids and ids[0] else None

    if site_id == "giathuoctot":
        m = _GIATHUOCTOT_RE.search(url)
        return m.group(1) if m else None

    if site_id == "thuoctot3mien":
        m = _THUOCTOT3MIEN_RE.search(url)
        return m.group(1) if m else None

    if site_id == "thuocsi":
        path = urlsplit(url).path.strip("/")
        if not path:
            return None
        return path.rsplit("/", 1)[-1]

    if site_id in ("duocphamgiasi", "thuochapu"):
        return url

    if site_id == "thuocsisaigon":
        parts = urlsplit(url)
        if not parts.path:
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

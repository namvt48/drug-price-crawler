"""Áp dụng whitelist filter lên kết quả crawl.

Ví von: như tấm lưới lọc ở cuối băng chuyền — chỉ giữ lại sản phẩm khớp
danh sách quan tâm (từ khóa tên / hãng sản xuất / khoảng giá), phần còn lại
cho qua để CSV/GUI không bị nhiễu bởi hàng nghìn thuốc không liên quan.
"""

from __future__ import annotations

from utils.models import DrugPrice, FilterConfig
from utils.normalizer import strip_accents


def _norm(s: str) -> str:
    return strip_accents(s or "").lower().strip()


def matches(price: DrugPrice, cfg: FilterConfig) -> bool:
    """Một bản ghi có lọt qua filter không (danh sách rỗng = pass tiêu chí đó)."""
    if cfg.name_keywords:
        name = _norm(price.drug_name)
        if not any(_norm(kw) in name for kw in cfg.name_keywords):
            return False
    if cfg.manufacturers:
        maker = _norm(price.manufacturer)
        if not any(_norm(m) in maker for m in cfg.manufacturers):
            return False
    if cfg.min_price_vnd > 0 and price.price_vnd < cfg.min_price_vnd:
        return False
    if cfg.max_price_vnd > 0 and price.price_vnd > cfg.max_price_vnd:
        return False
    return True


def apply_filters(prices: list[DrugPrice], cfg: FilterConfig | None) -> list[DrugPrice]:
    """Lọc danh sách theo FilterConfig; cfg None/không active thì trả nguyên vẹn."""
    if cfg is None or not cfg.is_active():
        return prices
    return [p for p in prices if matches(p, cfg)]

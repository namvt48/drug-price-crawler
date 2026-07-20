"""Data models cho crawler: SourceName, DrugPrice, SiteConfig.

Ví von: đây là "khuôn đúc" — mọi sản phẩm từ 9 nguồn khác nhau đều được đổ
về cùng một khuôn `DrugPrice` để CSV/GUI chỉ phải hiểu một hình dạng duy nhất.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator


class SourceName(str, Enum):
    """9 nguồn B2B. Giá trị = nhãn hiển thị trong CSV/GUI."""

    GIATHUOCTOT = "Giathuoctot"
    CHOTHUOC247 = "ChoThuoc247"
    THUOCHAPU = "ThuocHaPu"
    CHOTHUOCTOT = "ChoThuocTot"
    THUOCSI = "ThuocSi"
    THUOCTOT3MIEN = "ThuocTot3Mien"
    THUOCSISAIGON = "ThuocSiSaiGon"
    DUOCPHAMGIASI = "DuocPhamGiaSi"
    BACHHOATHUOC = "BachHoaThuoc"


class DrugPrice(BaseModel):
    """Một bản ghi giá thuốc đã chuẩn hoá từ bất kỳ nguồn nào."""

    drug_name: str
    canonical_name: str = ""
    brand: str = ""
    manufacturer: str = ""
    dosage_form: str = ""
    strength: str = ""
    price_vnd: int = 0          # 25000 — dùng int (VND không có phần thập phân)
    price_display: str = ""     # "25.000đ"
    source: SourceName
    source_url: str = ""
    product_id: str = ""        # ID nội bộ nguồn (slug/id/url) — match watchlist, KHÔNG vào CSV
    image_url: str = ""         # URL ảnh sản phẩm — hiển thị ở search/select, KHÔNG vào CSV
    crawled_at: datetime = Field(default_factory=datetime.now)

    @field_validator(
        "drug_name", "canonical_name", "brand", "manufacturer", "dosage_form", "strength", "price_display", "source_url", "product_id", "image_url",
        mode="before",
    )
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        """Nguồn API/HTML hay trả None cho field thiếu → ép về chuỗi rỗng."""
        return "" if v is None else v


# Thứ tự cột CSV — dùng chung cho CsvWriter và header file.
CSV_HEADERS: list[str] = [
    "drug_name",
    "canonical_name",
    "brand",
    "manufacturer",
    "dosage_form",
    "strength",
    "price_vnd",
    "price_display",
    "source",
    "source_url",
    "crawled_at",
]


class Credentials(BaseModel):
    username: str = ""
    password: str = ""


class AuthConfig(BaseModel):
    method: str = "form_login"        # form_login | api_login | cookie_inject
    session_key: str = "session_id"
    expiry_hours: int = 12
    retry_on_401: bool = True
    max_auth_retries: int = 3
    # Token dán tay cho site khó login (chothuoctot / bachhoathuoc).
    manual_token: str = ""


class CacheConfig(BaseModel):
    enabled: bool = True
    ttl_hours: int = 24


class RateLimitConfig(BaseModel):
    delay_seconds: float = 2.0
    max_retries: int = 3
    retry_backoff_seconds: float = 5.0


class FilterConfig(BaseModel):
    """Whitelist filter toàn cục (block `filters:` trong accounts.yaml).

    Danh sách rỗng = không lọc tiêu chí đó. So khớp không phân biệt hoa thường
    và không dấu (logic nằm ở utils/filters.py để tránh import vòng normalizer).
    """

    name_keywords: list[str] = Field(default_factory=list)   # tên thuốc phải chứa 1 trong các từ khóa
    manufacturers: list[str] = Field(default_factory=list)   # hãng sản xuất phải khớp 1 trong danh sách
    min_price_vnd: int = 0                                   # 0 = không giới hạn
    max_price_vnd: int = 0                                   # 0 = không giới hạn

    def is_active(self) -> bool:
        return bool(
            self.name_keywords or self.manufacturers
            or self.min_price_vnd > 0 or self.max_price_vnd > 0
        )


class SiteConfig(BaseModel):
    """Cấu hình 1 site sau khi đã merge với `defaults` từ accounts.yaml."""

    id: str                            # key trong YAML, vd "giathuoctot"
    name: str = ""
    enabled: bool = True
    base_url: str = ""
    login_url: str = ""
    search_url: str = ""
    credentials: Credentials = Field(default_factory=Credentials)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )


class CatalogItem(BaseModel):
    """Một sản phẩm trong catalog (chỉ id + tên + thông tin cơ bản, KHÔNG có giá).

    Ví von: như danh mục sản phẩm treo ở quầy — khách xem tên, chọn món rồi
    mới hỏi giá. Catalog crawl 1 lần, TTL dài (7 ngày) vì tên/NSX ít đổi.
    """

    product_id: str
    drug_name: str
    search_name: str = ""   # strip_accents(drug_name).lower() — cho matching không dấu
    manufacturer: str = ""
    source: SourceName
    source_url: str = ""
    image_url: str = ""
    cached_at: datetime = Field(default_factory=datetime.now)
    master_product_id: str = ""  # id nhóm entity-resolution (catalog_master_entity_resolved.xlsx)

    @field_validator(
        "drug_name", "manufacturer", "source_url", "search_name", "image_url", "master_product_id",
        mode="before",
    )
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return "" if v is None else v


class WatchlistItem(BaseModel):
    """Một sản phẩm user chọn theo dõi giá.

    Ví von: như "sổ ghi món cần hỏi giá" — user chọn từ catalog, hệ thống
    định kỳ hỏi giá từng món, cập nhật last_price_vnd + last_checked.
    """

    site_id: str            # "giathuoctot" — khớp CRAWLER_REGISTRY, dùng fetch giá
    product_id: str
    source: SourceName      # SourceName enum — dùng hiển thị + history
    drug_name: str
    search_name: str = ""   # keyword để query giá (search endpoint)
    image_url: str = ""     # URL ảnh — hiển thị khi load starred trên startup
    added_at: float = 0.0       # epoch seconds
    last_price_vnd: int = 0
    last_checked: float = 0.0   # epoch seconds

    @field_validator("drug_name", "search_name", "image_url", mode="before")
    @classmethod
    def _none_to_empty(cls, v: object) -> object:
        return "" if v is None else v


class WatchlistConfig(BaseModel):
    """Cấu hình watchlist (block `watchlist:` trong accounts.yaml).

    Ví von: như cài đặt chuông báo giá — bao lâu ring một lần, bao lâu
    phải rà lại danh mục.
    """

    refresh_interval_minutes: int = 10   # chu kỳ refresh giá watchlist

"""9 crawler B2B + registry ánh xạ site_id -> class crawler."""

from __future__ import annotations

from .bachhoathuoc import BachHoaThuocCrawler
from .chothuoc247 import ChoThuoc247Crawler
from .chothuoctot import ChoThuocTotCrawler
from .duocphamgiasi import DuocPhamGiaSiCrawler
from .giathuoctot import GiathuoctotCrawler
from .thuochapu import ThuocHaPuCrawler
from .thuocsi import ThuocSiCrawler
from .thuocsisaigon import ThuocSiSaiGonCrawler
from .thuoctot3mien import ThuocTot3MienCrawler

# site_id (khớp key trong accounts.yaml) -> class crawler.
CRAWLER_REGISTRY = {
    "giathuoctot": GiathuoctotCrawler,
    "chothuoc247": ChoThuoc247Crawler,
    "thuochapu": ThuocHaPuCrawler,
    "chothuoctot": ChoThuocTotCrawler,
    "thuocsi": ThuocSiCrawler,
    "thuoctot3mien": ThuocTot3MienCrawler,
    "thuocsisaigon": ThuocSiSaiGonCrawler,
    "duocphamgiasi": DuocPhamGiaSiCrawler,
    "bachhoathuoc": BachHoaThuocCrawler,
}

__all__ = ["CRAWLER_REGISTRY"]

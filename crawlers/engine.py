"""CrawlerEngine — điều phối crawl nhiều site song song, có cache.

Ví von: như bếp trưởng giao cùng một order ("boganic") cho nhiều đầu bếp
(9 site) cùng lúc, ai xong trước dọn trước, ai lỗi thì báo chứ không làm sập
cả bếp. Món nào còn trong tủ lạnh (cache còn hạn) thì lấy ra luôn.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from pathlib import Path

from utils.catalog_master import append_manual_product, append_or_update_listing, load_master_catalog
from utils.config_loader import app_base_dir, load_filters, load_sites, load_watchlist_config
from utils.filters import apply_filters
from utils.models import CatalogItem, DrugPrice, FilterConfig, SiteConfig, WatchlistItem
from utils.normalizer import strip_accents
from utils.url_detect import detect_product_id

from .base import BaseCrawler, CrawlError
from .b2b import CRAWLER_REGISTRY
from .cache_manager import CacheManager

LogFn = Callable[[str], None]
ProgressFn = Callable[[int, int], None]  # (done, total)


class CrawlerEngine:
    def __init__(
        self,
        config_path: str | Path | None = None,
        cache_db: str | Path | None = None,
        log: LogFn | None = None,
        use_cache: bool = True,
    ):
        self.sites: dict[str, SiteConfig] = load_sites(config_path)
        self.filters: FilterConfig = load_filters(config_path)
        self.watchlist_config = load_watchlist_config(config_path)
        self.cache = CacheManager(cache_db or (app_base_dir() / "output" / "cache.db"))
        self.log: LogFn = log or (lambda _m: None)
        self.use_cache = use_cache
        self._master_catalog: list[CatalogItem] | None = None
        # catalog_master_entity_resolved.xlsx có ~58k dòng — openpyxl mất vài chục
        # giây để đọc hết. Lock để GUI warm-up (thread nền, xem main_window
        # `_warm_catalog_worker`) và lần suggest_catalog đầu tiên (UI thread, nếu
        # gõ trước khi warm-up xong) không cùng đọc file 2 lần song song.
        self._catalog_lock = threading.Lock()

    def available_sites(self) -> list[SiteConfig]:
        """Site có crawler + enabled, giữ đúng thứ tự registry."""
        return [
            self.sites[sid]
            for sid in CRAWLER_REGISTRY
            if sid in self.sites and self.sites[sid].enabled
        ]

    async def crawl(
        self,
        keyword: str,
        site_ids: list[str] | None = None,
        progress: ProgressFn | None = None,
        force_refresh: bool = False,
    ) -> list[DrugPrice]:
        """`force_refresh=True`: bỏ qua cache đọc nhưng vẫn ghi cache + lịch sử giá
        (scheduler dùng để crawl mới định kỳ mà không mất history)."""
        targets = site_ids or [s.id for s in self.available_sites()]
        total = len(targets)
        done = 0
        results: list[DrugPrice] = []

        async def run(site_id: str) -> list[DrugPrice]:
            nonlocal done
            try:
                return await self._crawl_one(site_id, keyword, force_refresh)
            except Exception as exc:  # 1 site lỗi không kéo sập cả mẻ
                self.log(f"[{site_id}] LỖI: {exc}")
                return []
            finally:
                done += 1
                if progress:
                    progress(done, total)

        batches = await asyncio.gather(*(run(sid) for sid in targets))
        for batch in batches:
            results.extend(batch)

        results = self._apply_canonical(results)
        before = len(results)
        results = apply_filters(results, self.filters)
        if len(results) != before:
            self.log(f"Whitelist filter: giữ {len(results)}/{before} bản ghi.")
        self.log(f"Hoàn tất: {len(results)} bản ghi từ {total} nguồn.")
        return results

    def _apply_canonical(self, results: list[DrugPrice]) -> list[DrugPrice]:
        """Gán canonical_name cho từng bản ghi (gom biến thể liên nguồn)."""
        if not results:
            return results
        from utils.normalizer import canonical_for, group_names, load_aliases

        names = [p.drug_name for p in results]
        groups = group_names(names)
        var_to_canon: dict[str, str] = {}
        for canon, variants in groups.items():
            for v in variants:
                var_to_canon[v] = canon
        aliases = load_aliases()
        for p in results:
            key = p.drug_name.strip().lower()
            if aliases and key in aliases:
                p.canonical_name = aliases[key]
            elif p.drug_name in var_to_canon:
                p.canonical_name = var_to_canon[p.drug_name]
            else:
                p.canonical_name = canonical_for(p.drug_name, aliases)
        return results

    async def _crawl_one(
        self, site_id: str, keyword: str, force_refresh: bool = False
    ) -> list[DrugPrice]:
        config = self.sites.get(site_id)
        crawler_cls = CRAWLER_REGISTRY.get(site_id)
        if config is None or crawler_cls is None:
            self.log(f"[{site_id}] Không có cấu hình hoặc crawler — bỏ qua.")
            return []

        # Site không lọc được theo keyword phía server (vd bachhoathuoc — trả nguyên
        # catalog dù truyền keyword gì) → luôn crawl/cache TOÀN BỘ dữ liệu (key rỗng,
        # TTL dài của site, qua crawl_all()) rồi tự lọc theo keyword thật ở đây, thay
        # vì quét lại cả catalog mỗi lần search. Dùng cho luồng CLI/batch export;
        # luồng GUI interactive (chọn 1 sản phẩm) dùng fetch_live_prices() riêng, nhanh
        # hơn và luôn tươi, không qua cache này.
        supports_keyword = getattr(crawler_cls, "keyword_search_supported", True)
        cache_key = keyword if supports_keyword else ""

        # 1. Cache.
        if self.use_cache and config.cache.enabled and not force_refresh:
            cached = self.cache.get(site_id, cache_key)
            if cached is not None:
                age = self.cache.age_hours(site_id, cache_key) or 0
                self.log(f"[{config.name}] Cache hit ({age:.1f}h) → {len(cached)} sản phẩm.")
                return cached if supports_keyword else self._filter_local(cached, keyword)

        # 2. Crawl thật.
        crawler: BaseCrawler = crawler_cls(config, log=self.log)
        async with crawler:
            data = await crawler.crawl(keyword) if supports_keyword else await crawler.crawl_all()

        # 3. Lưu cache.
        if self.use_cache and config.cache.enabled and data:
            self.cache.set(site_id, cache_key, data, config.cache.ttl_hours)
        return data if supports_keyword else self._filter_local(data, keyword)

    @staticmethod
    def _filter_local(data: list[DrugPrice], keyword: str) -> list[DrugPrice]:
        """Lọc theo keyword phía client — dùng khi site không lọc được ở server."""
        kw = keyword.strip().lower()
        if not kw:
            return data
        return [d for d in data if kw in d.drug_name.lower()]

    async def fetch_live_prices(self, items: list[CatalogItem]) -> list[DrugPrice]:
        """Giá LIVE cho các catalog item user vừa chọn (GUI search-select) — không
        bao giờ đọc từ cache giá dài hạn. Site lọc được keyword ở server: search lại
        đúng tên sản phẩm đó (force_refresh, nhanh vì server tự thu hẹp kết quả).
        Site không lọc được (vd bachhoathuoc): gọi thẳng `fetch_price_by_id`, bỏ qua
        `crawl_all()`/cache hoàn toàn.

        Gom `items` theo site_id, chạy SONG SONG GIỮA CÁC SITE (mỗi site 1
        client/rate-limiter riêng, tổng thời gian ~ site chậm nhất) nhưng
        TRONG 1 SITE chỉ mở 1 crawler/login DUY NHẤT rồi dùng lại tuần tự cho
        mọi item cùng site — không phải 1 crawler/login riêng mỗi item.

        Lý do bắt buộc: 1 nhóm sản phẩm (gộp theo canonical_key) có thể chứa
        NHIỀU CatalogItem của CÙNG 1 site (site sàn đa nhà cung cấp như
        thuocsi thường có vài SKU trùng cho cùng 1 thuốc). Trước đây mỗi item
        tự tạo crawler + login riêng; chạy song song (`asyncio.gather` theo
        item) nghĩa là bắn hàng chục login ĐỒNG THỜI vào CÙNG 1 site chỉ vì 1
        nhóm có nhiều item — đã khiến ThuocSi tưởng bị tấn công và tự khoá IP
        (HTTP 403 "đăng nhập quá nhiều lần", xảy ra thật trong log). Gom theo
        site trước rồi login 1 lần/site giải quyết tận gốc.
        """
        by_site: dict[str, list[CatalogItem]] = {}
        for item in items:
            by_site.setdefault(self._site_id_for_source(item.source), []).append(item)

        async def fetch_site(site_id: str, site_items: list[CatalogItem]) -> list[DrugPrice]:
            crawler_cls = CRAWLER_REGISTRY.get(site_id)
            config = self.sites.get(site_id)
            if crawler_cls is None or config is None:
                self.log(f"[{site_id}] Không có cấu hình/crawler — bỏ qua live-fetch.")
                return []

            supports_keyword = getattr(crawler_cls, "keyword_search_supported", True)
            crawler: BaseCrawler = crawler_cls(config, log=self.log)
            site_results: list[DrugPrice] = []
            try:
                async with crawler:
                    for item in site_items:
                        if supports_keyword:
                            try:
                                matches = await crawler.crawl(item.drug_name)
                            except Exception as exc:
                                self.log(f"[{site_id}] Lỗi lấy giá live '{item.drug_name}': {exc}")
                                continue
                            if self.use_cache and config.cache.enabled and matches:
                                self.cache.set(site_id, item.drug_name, matches, config.cache.ttl_hours)
                            if item.product_id:
                                by_id = [m for m in matches if m.product_id == item.product_id]
                                matches = by_id or matches
                            site_results.extend(matches)
                        else:
                            try:
                                price = await crawler.fetch_price_by_id(item.product_id)
                            except Exception as exc:
                                self.log(f"[{site_id}] Lỗi lấy giá live theo id '{item.product_id}': {exc}")
                                continue
                            if price is not None:
                                site_results.append(price)
                                self.cache.record_history([price])
            except Exception as exc:
                self.log(f"[{site_id}] Lỗi live-fetch: {exc}")
            return site_results

        batches = await asyncio.gather(
            *(fetch_site(sid, site_items) for sid, site_items in by_site.items())
        )
        results: list[DrugPrice] = []
        for batch in batches:
            results.extend(batch)
        return results

    def suggest_names(self, prefix: str, limit: int = 30) -> list[str]:
        return self.cache.suggest_names(prefix, limit)

    def _ensure_master_catalog(self) -> list[CatalogItem]:
        """Nạp catalog_master_entity_resolved.xlsx 1 lần duy nhất, LAZY — engine
        chỉ dùng cho fetch_live_prices() (mỗi lần user thêm sản phẩm, main_window
        tạo 1 engine riêng cho việc này) không tốn công đọc file ~58k dòng mỗi lần."""
        if self._master_catalog is None:
            with self._catalog_lock:
                if self._master_catalog is None:
                    self._master_catalog = load_master_catalog(log=self.log)
        return self._master_catalog

    def warm_master_catalog(self) -> int:
        """Nạp trước catalog (gọi từ thread nền lúc app khởi động — xem
        main_window `_warm_catalog_worker`) để lần gõ tìm đầu tiên của user không
        phải đợi ~vài chục giây đọc file ngay trên UI thread. Trả về số sản phẩm."""
        return len(self._ensure_master_catalog())

    def add_manual_product(self, urls: dict[str, str], canonical_name: str) -> list[CatalogItem]:
        """Thêm 1 sản phẩm MỚI thủ công qua GUI (dán URL từng site — xem
        `gui.main_window._save_manual_product`): tách `product_id` CƠ HỌC từ URL
        (`utils.url_detect`, không gọi mạng), ghi vào
        catalog_master_entity_resolved.xlsx (`utils.catalog_master.append_manual_product`
        — CHẬM, PHẢI gọi trong thread nền phía GUI), rồi thêm luôn vào
        `self._master_catalog` đang có sẵn trong bộ nhớ (nếu đã warm-up) để có ngay,
        không cần đợi đọc lại cả file.

        `urls`: {site_id: url} — site rỗng/không tách được product_id bị bỏ qua,
        không chặn các site khác. Trả về [] nếu KHÔNG site nào tách được (không ghi
        gì vào file trong trường hợp đó)."""
        items: list[CatalogItem] = []
        for site_id, url in urls.items():
            url = (url or "").strip()
            if not url:
                continue
            product_id = detect_product_id(site_id, url)
            if not product_id:
                continue
            source = getattr(CRAWLER_REGISTRY.get(site_id), "source_name", None)
            if source is None:
                continue
            items.append(CatalogItem(
                product_id=product_id,
                drug_name=canonical_name,
                search_name=strip_accents(canonical_name).lower(),
                source=source,
                source_url=url,
            ))

        if not items:
            return []

        master_id = append_manual_product(items, canonical_name)
        for item in items:
            item.master_product_id = master_id

        # Lock chung với _ensure_master_catalog — thêm sản phẩm mới thường chạy ở
        # thread nền (xem gui.main_window._save_manual_product_worker) trong khi UI
        # thread có thể đang gọi suggest_catalog() đọc cùng list này.
        with self._catalog_lock:
            if self._master_catalog is not None:
                self._master_catalog.extend(items)

        return items

    def set_manual_listing(
        self, master_product_id: str, site_id: str, url: str, canonical_name: str
    ) -> CatalogItem | None:
        """Thêm/sửa URL 1 site cho 1 sản phẩm ĐÃ CÓ trong 'Đã chọn' (gắn vào
        `master_product_id` có sẵn — xem
        `gui.main_window._on_detail_row_double_click`). Khác `add_manual_product`
        (luôn tạo sản phẩm/`master_product_id` MỚI). Trả CatalogItem mới nếu tách
        `product_id` thành công + ghi file OK; None nếu URL không tách được (không
        ghi gì) — GUI tự bỏ qua, không chặn thao tác khác."""
        product_id = detect_product_id(site_id, url)
        if not product_id:
            return None
        source = getattr(CRAWLER_REGISTRY.get(site_id), "source_name", None)
        if source is None:
            return None

        item = CatalogItem(
            product_id=product_id,
            drug_name=canonical_name,
            search_name=strip_accents(canonical_name).lower(),
            source=source,
            source_url=url,
            master_product_id=master_product_id,
        )
        append_or_update_listing(master_product_id, item, canonical_name)

        with self._catalog_lock:
            if self._master_catalog is not None:
                self._master_catalog = [
                    it for it in self._master_catalog
                    if not (it.master_product_id == master_product_id and it.source == source)
                ]
                self._master_catalog.append(item)

        return item

    def suggest_catalog(self, prefix: str, limit: int = 30) -> list[CatalogItem]:
        """Trả tối đa `limit` nhóm khớp, gồm đủ mọi listing/site của từng nhóm."""
        q = prefix.strip().lower()
        catalog = self._ensure_master_catalog()
        selected_groups: set[str] = set()
        for item in catalog:
            if q and q not in item.drug_name.lower() and q not in item.search_name:
                continue
            group_key = item.master_product_id or item.drug_name
            if group_key not in selected_groups and len(selected_groups) >= limit:
                continue
            selected_groups.add(group_key)
        return [
            item
            for item in catalog
            if (item.master_product_id or item.drug_name) in selected_groups
        ]

    def find_by_name(self, name: str):
        return self.cache.find_by_name(name)

    def all_cached_names(self) -> list[str]:
        return self.cache.all_live_names()

    def find_by_names(self, names: list[str]):
        return self.cache.find_by_names(names)

    def get_history(self, drug_name: str) -> list[dict]:
        return self.cache.get_history(drug_name)

    # ----------------------------------------------------- watchlist
    async def refresh_watchlist_prices(self) -> int:
        """Refresh prices for all watchlist items.

        Groups by site_id → for each: ensure_auth (via crawler.crawl) →
        search by search_name → match by product_id → update price.
        """
        by_source = self.cache.get_watchlist_by_source()
        if not by_source:
            return 0
        updated = 0
        all_prices: list[DrugPrice] = []
        for site_id, items in by_source.items():
            config = self.sites.get(site_id)
            crawler_cls = CRAWLER_REGISTRY.get(site_id)
            if config is None or crawler_cls is None:
                self.log(f"[{site_id}] Không có config/crawler — bỏ qua watchlist.")
                continue
            search_terms = list({item.search_name for item in items if item.search_name})
            crawler: BaseCrawler = crawler_cls(config, log=self.log)
            try:
                async with crawler:
                    for term in search_terms:
                        try:
                            results = await crawler.crawl(term)
                        except Exception as exc:
                            self.log(f"[{site_id}] Lỗi fetch giá '{term}': {exc}")
                            continue
                        all_prices.extend(results)
                        by_pid = {r.product_id: r for r in results if r.product_id}
                        now = time.time()
                        for item in items:
                            if item.search_name != term:
                                continue
                            match = by_pid.get(item.product_id)
                            if match and match.price_vnd > 0:
                                self.cache.update_watchlist_price(
                                    item.product_id, site_id, match.price_vnd, now
                                )
                                updated += 1
            except Exception as exc:
                self.log(f"[{site_id}] LỖI refresh watchlist: {exc}")
        if all_prices:
            self.cache.record_history(all_prices)
        self.log(f"Watchlist refresh: {updated} mục cập nhật.")
        return updated

    def add_to_watchlist(self, catalog_item: CatalogItem) -> None:
        site_id = self._site_id_for_source(catalog_item.source)
        item = WatchlistItem(
            site_id=site_id,
            product_id=catalog_item.product_id,
            source=catalog_item.source,
            drug_name=catalog_item.drug_name,
            search_name=catalog_item.search_name or strip_accents(catalog_item.drug_name).lower(),
            image_url=catalog_item.image_url,
            added_at=time.time(),
        )
        self.cache.add_to_watchlist(item)

    def remove_from_watchlist(self, product_id: str, site_id: str) -> bool:
        return self.cache.remove_from_watchlist(product_id, site_id)

    def get_watchlist(self) -> list[WatchlistItem]:
        return self.cache.get_watchlist()

    def get_starred(self) -> list[WatchlistItem]:
        """Load starred/watchlist items on startup — same as get_watchlist().
        Entry point for GUI to display saved items immediately on launch.
        """
        return self.cache.get_watchlist()

    @staticmethod
    def _site_id_for_source(source) -> str:
        for sid in CRAWLER_REGISTRY:
            crawler_cls = CRAWLER_REGISTRY[sid]
            if hasattr(crawler_cls, "source_name") and crawler_cls.source_name == source:
                return sid
        return source.value.lower() if hasattr(source, "value") else str(source).lower()

    def close(self) -> None:
        self.cache.close()

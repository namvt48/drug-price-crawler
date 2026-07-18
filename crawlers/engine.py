"""CrawlerEngine — điều phối crawl nhiều site song song, có cache.

Ví von: như bếp trưởng giao cùng một order ("boganic") cho nhiều đầu bếp
(9 site) cùng lúc, ai xong trước dọn trước, ai lỗi thì báo chứ không làm sập
cả bếp. Món nào còn trong tủ lạnh (cache còn hạn) thì lấy ra luôn.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path

from utils.config_loader import app_base_dir, load_filters, load_sites, load_watchlist_config
from utils.filters import apply_filters
from utils.models import CatalogItem, DrugPrice, FilterConfig, SiteConfig, WatchlistItem
from utils.normalizer import strip_accents

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
        """
        results: list[DrugPrice] = []
        for item in items:
            site_id = self._site_id_for_source(item.source)
            crawler_cls = CRAWLER_REGISTRY.get(site_id)
            config = self.sites.get(site_id)
            if crawler_cls is None or config is None:
                self.log(f"[{site_id}] Không có cấu hình/crawler — bỏ qua live-fetch.")
                continue

            supports_keyword = getattr(crawler_cls, "keyword_search_supported", True)
            if supports_keyword:
                try:
                    matches = await self._crawl_one(site_id, item.drug_name, force_refresh=True)
                except Exception as exc:
                    self.log(f"[{site_id}] Lỗi lấy giá live '{item.drug_name}': {exc}")
                    continue
                if item.product_id:
                    by_id = [m for m in matches if m.product_id == item.product_id]
                    matches = by_id or matches
                results.extend(matches)
                continue

            crawler: BaseCrawler = crawler_cls(config, log=self.log)
            try:
                async with crawler:
                    price = await crawler.fetch_price_by_id(item.product_id)
            except Exception as exc:
                self.log(f"[{site_id}] Lỗi lấy giá live theo id '{item.product_id}': {exc}")
                continue
            if price is not None:
                results.append(price)
                self.cache.record_history([price])
        return results

    def suggest_names(self, prefix: str, limit: int = 30) -> list[str]:
        return self.cache.suggest_names(prefix, limit)

    def suggest_catalog(self, prefix: str, limit: int = 30) -> list[CatalogItem]:
        return self.cache.catalog_suggest(prefix, limit)

    def find_catalog_item_by_url(self, url: str) -> CatalogItem | None:
        return self.cache.catalog_find_by_url(url)

    def find_by_name(self, name: str):
        return self.cache.find_by_name(name)

    def all_cached_names(self) -> list[str]:
        return self.cache.all_live_names()

    def find_by_names(self, names: list[str]):
        return self.cache.find_by_names(names)

    def get_history(self, drug_name: str) -> list[dict]:
        return self.cache.get_history(drug_name)

    # ----------------------------------------------------- catalog + watchlist
    async def crawl_catalog(
        self,
        site_ids: list[str] | None = None,
        force_refresh: bool = False,
        progress: ProgressFn | None = None,
    ) -> int:
        """Crawl catalog (all products, id+name only) for selected sites.

        Reuses crawler.crawl('') → converts to CatalogItem → upserts.
        Skips sites with fresh catalog (age < catalog_ttl_hours) unless force_refresh.
        `progress(done, total)` được gọi sau MỖI site (dù skip/lỗi/thành công) — dùng
        cho GUI full-scan hiển thị tiến độ (xem gui/main_window.py `_run_full_scan_worker`).
        """
        targets = site_ids or [s.id for s in self.available_sites()]
        total = len(targets)
        total_stored = 0
        for done, site_id in enumerate(targets, start=1):
            try:
                config = self.sites.get(site_id)
                crawler_cls = CRAWLER_REGISTRY.get(site_id)
                if config is None or crawler_cls is None:
                    continue
                if not force_refresh:
                    source_name = getattr(crawler_cls, "source_name", None)
                    source_val = source_name.value if source_name else (config.name or site_id)
                    age = self.cache.catalog_age_hours(source_val)
                    if age is not None and age < self.watchlist_config.catalog_ttl_hours:
                        self.log(f"[{site_id}] Catalog fresh ({age:.1f}h) — bỏ qua.")
                        continue
                crawler: BaseCrawler = crawler_cls(config, log=self.log)
                try:
                    async with crawler:
                        prices = await crawler.crawl_all()
                except Exception as exc:
                    self.log(f"[{site_id}] LỖI catalog: {exc}")
                    continue
                items = [
                    CatalogItem(
                        product_id=p.product_id,
                        drug_name=p.drug_name,
                        search_name=strip_accents(p.drug_name).lower(),
                        manufacturer=p.manufacturer,
                        source=p.source,
                        source_url=p.source_url,
                        image_url=p.image_url,
                    )
                    for p in prices
                    if p.drug_name and p.product_id
                ]
                stored = self.cache.upsert_catalog_items(items)
                total_stored += stored
                self.log(f"[{site_id}] Catalog: {stored} mục.")
            finally:
                if progress:
                    progress(done, total)
        return total_stored

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

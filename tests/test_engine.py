"""Tests cho crawlers.engine.CrawlerEngine."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from crawlers.engine import CrawlerEngine
from utils.models import CatalogItem, DrugPrice, SiteConfig, SourceName, WatchlistItem


def _write_config(path: Path) -> None:
    path.write_text(
        """
defaults:
  cache:
    enabled: true
    ttl_hours: 24
  rate_limit:
    delay_seconds: 0
    max_retries: 2
    retry_backoff_seconds: 0

sites:
  giathuoctot:
    name: "Gia Thuoc Tot"
    base_url: "https://www.giathuoctot.com"
    credentials:
      username: "u"
      password: "p"
  chothuoc247:
    enabled: false
    name: "Cho Thuoc 247"
""",
        encoding="utf-8",
    )


def _dp(name: str = "Test", source: SourceName = SourceName.GIATHUOCTOT, price: int = 1000) -> DrugPrice:
    return DrugPrice(drug_name=name, price_vnd=price, source=source)


class TestAvailableSites:
    def test_returns_enabled_in_registry_order(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db")
        sites = engine.available_sites()
        ids = [s.id for s in sites]
        assert "giathuoctot" in ids
        assert "chothuoc247" not in ids
        engine.close()

    def test_disabled_excluded(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db")
        sites = engine.available_sites()
        for s in sites:
            assert s.enabled is True
        engine.close()


class TestCrawl:
    def test_crawl_aggregates_results(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        async def fake_crawl_one(self: CrawlerEngine, site_id: str, keyword: str, force_refresh: bool = False) -> list[DrugPrice]:
            return [_dp(f"Drug_{site_id}", SourceName.GIATHUOCTOT, 100)]

        monkeypatch.setattr(CrawlerEngine, "_crawl_one", fake_crawl_one)
        results = asyncio.run(engine.crawl("kw"))
        assert len(results) >= 1
        assert all(r.drug_name.startswith("Drug_") for r in results)
        engine.close()

    def test_progress_callback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        async def fake_crawl_one(self: CrawlerEngine, site_id: str, keyword: str, force_refresh: bool = False) -> list[DrugPrice]:
            return [_dp()]

        monkeypatch.setattr(CrawlerEngine, "_crawl_one", fake_crawl_one)
        calls: list[tuple[int, int]] = []
        asyncio.run(engine.crawl("kw", progress=lambda done, total: calls.append((done, total))))
        assert len(calls) >= 1
        assert calls[-1][0] == calls[-1][1]
        engine.close()

    def test_site_exception_isolated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        async def fake_crawl_one(self: CrawlerEngine, site_id: str, keyword: str, force_refresh: bool = False) -> list[DrugPrice]:
            if site_id == "giathuoctot":
                raise RuntimeError("boom")
            return [_dp("OK")]

        monkeypatch.setattr(CrawlerEngine, "_crawl_one", fake_crawl_one)
        results = asyncio.run(engine.crawl("kw"))
        # Failed site returns [], others still work.
        assert all(r.drug_name == "OK" for r in results)
        engine.close()

    def test_cache_hit_skips_crawl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=True)
        cached_data = [_dp("Cached", SourceName.GIATHUOCTOT, 999)]
        engine.cache.set("giathuoctot", "kw", cached_data, ttl_hours=24)

        crawl_called = False

        class _FakeCrawler:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def crawl(self, keyword):
                nonlocal crawl_called
                crawl_called = True
                return [_dp("Live")]

        from crawlers import engine as engine_mod

        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)
        results = asyncio.run(engine.crawl("kw", site_ids=["giathuoctot"]))
        assert not crawl_called
        assert len(results) == 1
        assert results[0].drug_name == "Cached"
        engine.close()

    def test_crawl_one_no_config_returns_empty(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        result = asyncio.run(engine._crawl_one("nonexistent", "kw"))
        assert result == []
        engine.close()


class TestKeywordSearchUnsupported:
    """Site không lọc được theo keyword phía server (vd bachhoathuoc) — engine phải
    cache toàn catalog (keyword rỗng) rồi tự lọc theo keyword thật."""

    @staticmethod
    def _fake_crawler_cls(received: list[str], items: list[DrugPrice]):
        class _FakeCrawler:
            keyword_search_supported = False

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def crawl(self, keyword):
                received.append(keyword)
                return items

            async def crawl_all(self):
                return await self.crawl("")

        return _FakeCrawler

    def test_crawls_full_catalog_and_filters_locally(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=True)
        received: list[str] = []
        items = [_dp("Boganic hop", price=1000), _dp("Paracetamol", price=2000)]

        from crawlers import engine as engine_mod

        monkeypatch.setitem(
            engine_mod.CRAWLER_REGISTRY, "giathuoctot", self._fake_crawler_cls(received, items)
        )
        results = asyncio.run(engine.crawl("boganic", site_ids=["giathuoctot"]))

        assert received == [""]  # luôn crawl full catalog, không phải "boganic"
        assert [r.drug_name for r in results] == ["Boganic hop"]
        # Cache lưu dưới key rỗng (toàn catalog), không phải theo "boganic".
        assert engine.cache.get("giathuoctot", "") is not None
        assert engine.cache.get("giathuoctot", "boganic") is None
        engine.close()

    def test_second_search_reuses_full_catalog_cache_without_new_crawl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=True)
        received: list[str] = []
        items = [_dp("Boganic hop", price=1000), _dp("Paracetamol", price=2000)]

        from crawlers import engine as engine_mod

        monkeypatch.setitem(
            engine_mod.CRAWLER_REGISTRY, "giathuoctot", self._fake_crawler_cls(received, items)
        )
        asyncio.run(engine.crawl("boganic", site_ids=["giathuoctot"]))
        results2 = asyncio.run(engine.crawl("paracetamol", site_ids=["giathuoctot"]))

        assert received == [""]  # lần 2 dùng cache toàn catalog, không gọi crawler lại
        assert [r.drug_name for r in results2] == ["Paracetamol"]
        engine.close()


class TestForceRefresh:
    def test_force_refresh_skips_cache_read_but_writes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=True)
        engine.cache.set("giathuoctot", "kw", [_dp("Cached", price=999)], ttl_hours=24)

        class _FakeCrawler:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def crawl(self, keyword):
                return [_dp("Live", price=1234)]

        from crawlers import engine as engine_mod

        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)
        results = asyncio.run(
            engine.crawl("kw", site_ids=["giathuoctot"], force_refresh=True)
        )
        assert [r.drug_name for r in results] == ["Live"]
        # Cache đã được ghi đè bằng kết quả mới + history có mốc giá mới.
        cached = engine.cache.get("giathuoctot", "kw")
        assert cached is not None and cached[0].drug_name == "Live"
        assert engine.cache.get_history("Live")[0]["price_vnd"] == 1234
        engine.close()


class TestEngineFilters:
    def test_filters_applied_after_crawl(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        engine.filters.name_keywords = ["boganic"]

        async def fake_crawl_one(
            self: CrawlerEngine, site_id: str, keyword: str, force_refresh: bool = False
        ) -> list[DrugPrice]:
            return [_dp("Boganic vien"), _dp("Panadol Extra")]

        monkeypatch.setattr(CrawlerEngine, "_crawl_one", fake_crawl_one)
        results = asyncio.run(engine.crawl("kw", site_ids=["giathuoctot"]))
        assert [r.drug_name for r in results] == ["Boganic vien"]
        engine.close()

    def test_filters_loaded_from_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text(
            "sites:\n  giathuoctot:\n    name: G\n"
            "filters:\n  manufacturers: [Traphaco]\n",
            encoding="utf-8",
        )
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db")
        assert engine.filters.manufacturers == ["Traphaco"]
        engine.close()


class TestCanonicalDelegation:
    def test_all_cached_names_and_find_by_names(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db")
        engine.cache.set("s", "k", [_dp("X1"), _dp("X2")], ttl_hours=24)
        assert sorted(engine.all_cached_names()) == ["X1", "X2"]
        assert len(engine.find_by_names(["X1", "X2"])) == 2
        engine.close()

    def test_get_history_delegates(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db")
        engine.cache.record_history([_dp("H", price=700)])
        rows = engine.get_history("H")
        assert rows[0]["price_vnd"] == 700
        engine.close()


class TestCacheDelegation:
    def test_suggest_names(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db")
        engine.cache.set("s", "k", [_dp("Paracetamol")], ttl_hours=24)
        names = engine.suggest_names("para")
        assert "Paracetamol" in names
        engine.close()

    def test_find_by_name(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db")
        engine.cache.set("s", "k", [_dp("Aspirin", SourceName.GIATHUOCTOT, 100)], ttl_hours=24)
        results = engine.find_by_name("Aspirin")
        assert len(results) == 1
        assert results[0].drug_name == "Aspirin"
        engine.close()

    def test_find_catalog_item_by_url(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db")
        engine.cache.upsert_catalog_items([
            CatalogItem(
                product_id="p1",
                drug_name="Boganic Forte",
                source=SourceName.GIATHUOCTOT,
                source_url="https://example.com/p/boganic",
            ),
        ])
        item = engine.find_catalog_item_by_url("https://example.com/p/boganic")
        assert item is not None
        assert item.drug_name == "Boganic Forte"
        assert engine.find_catalog_item_by_url("https://example.com/p/missing") is None
        engine.close()

    def test_close_closes_cache(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db")
        engine.close()
        with pytest.raises(Exception):
            engine.cache.get("s", "k")


class _FakeCrawler:
    """Fake crawler for catalog/watchlist tests — returns preset DrugPrice list."""

    source_name = SourceName.GIATHUOCTOT

    def __init__(self, *args, **kwargs):
        self._keyword = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def crawl(self, keyword: str) -> list[DrugPrice]:
        self._keyword = keyword
        return [
            DrugPrice(
                drug_name="Boganic Forte",
                manufacturer="Traphaco",
                price_vnd=67000,
                source=SourceName.GIATHUOCTOT,
                source_url="https://example.com/p/boganic",
                product_id="boganic-slug",
            ),
            DrugPrice(
                drug_name="Paracetamol",
                manufacturer="Sanofi",
                price_vnd=5000,
                source=SourceName.GIATHUOCTOT,
                source_url="https://example.com/p/para",
                product_id="para-slug",
            ),
        ]

    async def crawl_all(self) -> list[DrugPrice]:
        return await self.crawl("")


class TestCrawlCatalog:
    def test_crawl_catalog_populates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        from crawlers import engine as engine_mod
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)

        count = asyncio.run(engine.crawl_catalog(site_ids=["giathuoctot"], force_refresh=True))
        assert count == 2
        assert engine.cache.catalog_count("Giathuoctot") == 2
        items = engine.cache.catalog_suggest("boga")
        assert len(items) == 1
        assert items[0].drug_name == "Boganic Forte"
        assert items[0].product_id == "boganic-slug"
        engine.close()

    def test_crawl_catalog_skips_fresh(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        from utils.models import CatalogItem
        from datetime import datetime
        engine.cache.upsert_catalog_items([
            CatalogItem(
                product_id="old-p1",
                drug_name="OldItem",
                search_name="olditem",
                source=SourceName.GIATHUOCTOT,
                cached_at=datetime.now(),
            ),
        ])

        crawl_called = False
        original_crawl = _FakeCrawler.crawl

        async def patched_crawl(self, keyword):
            nonlocal crawl_called
            crawl_called = True
            return await original_crawl(self, keyword)

        monkeypatch.setattr(_FakeCrawler, "crawl", patched_crawl)
        from crawlers import engine as engine_mod
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)

        count = asyncio.run(engine.crawl_catalog(site_ids=["giathuoctot"], force_refresh=False))
        assert not crawl_called
        assert count == 0
        engine.close()

    def test_crawl_catalog_error_isolated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        class _BoomCrawler:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def crawl(self, kw): raise RuntimeError("boom")

        from crawlers import engine as engine_mod
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _BoomCrawler)

        count = asyncio.run(engine.crawl_catalog(site_ids=["giathuoctot"], force_refresh=True))
        assert count == 0
        engine.close()

    def test_crawl_catalog_progress_callback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """progress(done, total) phải được gọi cho MỖI site trong site_ids — kể cả
        site không có config (nhánh skip sớm) — để GUI full-scan vẽ đúng tiến độ."""
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        from crawlers import engine as engine_mod
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)

        calls: list[tuple[int, int]] = []
        asyncio.run(
            engine.crawl_catalog(
                site_ids=["giathuoctot", "unknown_site"],
                force_refresh=True,
                progress=lambda done, total: calls.append((done, total)),
            )
        )
        assert calls == [(1, 2), (2, 2)]
        engine.close()


class TestRefreshWatchlist:
    def test_refresh_updates_prices(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        from crawlers import engine as engine_mod
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)

        engine.cache.add_to_watchlist(WatchlistItem(
            site_id="giathuoctot",
            product_id="boganic-slug",
            source=SourceName.GIATHUOCTOT,
            drug_name="Boganic Forte",
            search_name="boganic",
            added_at=0.0,
        ))

        updated = asyncio.run(engine.refresh_watchlist_prices())
        assert updated == 1
        items = engine.cache.get_watchlist()
        assert items[0].last_price_vnd == 67000
        assert items[0].last_checked > 0
        engine.close()

    def test_refresh_empty_watchlist(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        updated = asyncio.run(engine.refresh_watchlist_prices())
        assert updated == 0
        engine.close()

    def test_refresh_error_isolated(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        class _BoomCrawler:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
            async def crawl(self, kw): raise RuntimeError("boom")

        from crawlers import engine as engine_mod
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _BoomCrawler)

        engine.cache.add_to_watchlist(WatchlistItem(
            site_id="giathuoctot",
            product_id="p1",
            source=SourceName.GIATHUOCTOT,
            drug_name="X",
            search_name="x",
            added_at=0.0,
        ))

        updated = asyncio.run(engine.refresh_watchlist_prices())
        assert updated == 0
        engine.close()


class TestWatchlistDelegation:
    def test_add_and_get_watchlist(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        from utils.models import CatalogItem
        ci = CatalogItem(
            product_id="boganic-slug",
            drug_name="Boganic",
            search_name="boganic",
            manufacturer="Traphaco",
            source=SourceName.GIATHUOCTOT,
            source_url="https://example.com/p/1",
        )
        engine.add_to_watchlist(ci)
        items = engine.get_watchlist()
        assert len(items) == 1
        assert items[0].drug_name == "Boganic"
        assert items[0].site_id == "giathuoctot"
        engine.close()

    def test_remove_from_watchlist(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        from utils.models import CatalogItem
        ci = CatalogItem(
            product_id="p1",
            drug_name="X",
            source=SourceName.GIATHUOCTOT,
        )
        engine.add_to_watchlist(ci)
        assert engine.remove_from_watchlist("p1", "giathuoctot") is True
        assert engine.get_watchlist() == []
        engine.close()

    def test_suggest_catalog(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        from utils.models import CatalogItem
        engine.cache.upsert_catalog_items([
            CatalogItem(product_id="p1", drug_name="Boganic", source=SourceName.GIATHUOCTOT),
        ])
        results = engine.suggest_catalog("boga")
        assert len(results) == 1
        assert results[0].drug_name == "Boganic"
        engine.close()

    def test_get_starred_returns_watchlist(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        from utils.models import CatalogItem
        ci = CatalogItem(
            product_id="p1",
            drug_name="StarredDrug",
            source=SourceName.GIATHUOCTOT,
            image_url="https://img.test/starred.jpg",
        )
        engine.add_to_watchlist(ci)
        starred = engine.get_starred()
        assert len(starred) == 1
        assert starred[0].drug_name == "StarredDrug"
        assert starred[0].image_url == "https://img.test/starred.jpg"
        engine.close()

    def test_add_to_watchlist_passes_image_url(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        from utils.models import CatalogItem
        ci = CatalogItem(
            product_id="p1",
            drug_name="ImgDrug",
            source=SourceName.GIATHUOCTOT,
            image_url="https://img.test/pass.jpg",
        )
        engine.add_to_watchlist(ci)
        items = engine.get_watchlist()
        assert items[0].image_url == "https://img.test/pass.jpg"
        engine.close()


class TestFetchLivePrices:
    """engine.fetch_live_prices — giá LIVE cho catalog item user vừa chọn (GUI
    search-select), không bao giờ đọc cache giá dài hạn."""

    def test_supports_keyword_site_reruns_crawl_live(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=True)
        received: list[str] = []

        class _FakeCrawler:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def crawl(self, keyword):
                received.append(keyword)
                return [_dp("Boganic hop", price=1000)]

        from crawlers import engine as engine_mod

        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)
        item = CatalogItem(product_id="", drug_name="Boganic hop", source=SourceName.GIATHUOCTOT)
        results = asyncio.run(engine.fetch_live_prices([item]))

        assert received == ["Boganic hop"]  # search lại đúng tên, không phải cache
        assert [r.drug_name for r in results] == ["Boganic hop"]
        engine.close()

    def test_unsupported_keyword_site_uses_fetch_price_by_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=True)
        received_ids: list[str] = []

        class _FakeCrawler:
            keyword_search_supported = False

            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def fetch_price_by_id(self, product_id):
                received_ids.append(product_id)
                return _dp("Almetamin", price=70000)

        from crawlers import engine as engine_mod

        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)
        item = CatalogItem(product_id="sku-1", drug_name="Almetamin", source=SourceName.GIATHUOCTOT)
        results = asyncio.run(engine.fetch_live_prices([item]))

        assert received_ids == ["sku-1"]
        assert [r.drug_name for r in results] == ["Almetamin"]
        # record_history gọi trực tiếp (không qua crawl_cache) — vẫn giữ lịch sử giá.
        assert engine.cache.get_history("Almetamin")[0]["price_vnd"] == 70000
        engine.close()

    def test_matches_by_product_id_when_search_returns_multiple(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Site fuzzy-match có thể trả nhiều kết quả cho 1 từ khóa — ưu tiên đúng
        product_id đã biết từ catalog thay vì giữ hết."""
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=True)

        class _FakeCrawler:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def crawl(self, keyword):
                return [
                    DrugPrice(drug_name="Boganic hop", price_vnd=1000,
                              source=SourceName.GIATHUOCTOT, product_id="p1"),
                    DrugPrice(drug_name="Bổ Gan Abipha", price_vnd=2000,
                              source=SourceName.GIATHUOCTOT, product_id="p2"),
                ]

        from crawlers import engine as engine_mod

        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)
        item = CatalogItem(product_id="p1", drug_name="Boganic hop", source=SourceName.GIATHUOCTOT)
        results = asyncio.run(engine.fetch_live_prices([item]))

        assert len(results) == 1
        assert results[0].product_id == "p1"
        engine.close()

    def test_unknown_site_skipped(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        item = CatalogItem(product_id="p1", drug_name="X", source=SourceName.THUOCSI)
        results = asyncio.run(engine.fetch_live_prices([item]))
        assert results == []
        engine.close()

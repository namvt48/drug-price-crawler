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

        engine._master_catalog = [
            CatalogItem(product_id="p1", drug_name="Boganic", search_name="boganic",
                        source=SourceName.GIATHUOCTOT, master_product_id="MP1"),
        ]
        results = engine.suggest_catalog("boga")
        assert len(results) == 1
        assert results[0].drug_name == "Boganic"
        engine.close()

    def test_suggest_catalog_limit_keeps_every_source_in_selected_group(
        self, tmp_path: Path
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        engine._master_catalog = [
            CatalogItem(
                product_id="bht-1", drug_name="Alpha", source=SourceName.BACHHOATHUOC,
                master_product_id="MP1",
            ),
            CatalogItem(
                product_id="bht-2", drug_name="Beta", source=SourceName.BACHHOATHUOC,
                master_product_id="MP2",
            ),
            CatalogItem(
                product_id="hapu-1", drug_name="Alpha", source=SourceName.THUOCHAPU,
                master_product_id="MP1",
            ),
        ]

        results = engine.suggest_catalog("", limit=1)

        assert {item.source for item in results} == {
            SourceName.BACHHOATHUOC,
            SourceName.THUOCHAPU,
        }
        engine.close()

    def test_warm_master_catalog_loads_and_counts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """warm_master_catalog() (gọi từ thread nền GUI lúc khởi động) phải trigger
        đúng load 1 lần và trả về đúng số lượng, để _ensure_master_catalog sau đó
        không load lại (xem CrawlerEngine.__init__ comment về catalog_lock)."""
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        calls = 0

        def fake_load(path=None, log=None):
            nonlocal calls
            calls += 1
            return [CatalogItem(product_id="p1", drug_name="X", source=SourceName.GIATHUOCTOT)]

        import crawlers.engine as engine_mod
        monkeypatch.setattr(engine_mod, "load_master_catalog", fake_load)

        assert engine.warm_master_catalog() == 1
        assert engine.suggest_catalog("") == engine._master_catalog
        assert calls == 1
        engine.close()


class TestAddManualProduct:
    def test_adds_detected_sites_and_writes_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        saved = {}

        def fake_append(items, canonical_name, path=None):
            saved["items"] = items
            saved["name"] = canonical_name
            return "MP999999"

        import crawlers.engine as engine_mod
        monkeypatch.setattr(engine_mod, "append_manual_product", fake_append)

        items = engine.add_manual_product(
            {
                "giathuoctot": "https://www.giathuoctot.com/product/abc-def",
                "chothuoc247": "https://chothuoc247.vn/san-pham/999",
                "thuocsi": "",  # rỗng -> bỏ qua
                "chothuoctot": "https://chothuoctot.vn/khong-hop-le",  # không tách được -> bỏ qua
            },
            "Sản phẩm Test",
        )
        assert len(items) == 2
        assert {i.source for i in items} == {SourceName.GIATHUOCTOT, SourceName.CHOTHUOC247}
        assert all(i.master_product_id == "MP999999" for i in items)
        assert all(i.drug_name == "Sản phẩm Test" for i in items)
        assert saved["name"] == "Sản phẩm Test"
        engine.close()

    def test_no_sites_detected_returns_empty_and_does_not_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        called = False

        def fake_append(items, canonical_name, path=None):
            nonlocal called
            called = True
            return "MP1"

        import crawlers.engine as engine_mod
        monkeypatch.setattr(engine_mod, "append_manual_product", fake_append)

        items = engine.add_manual_product({"giathuoctot": "https://khong-hop-le.com"}, "X")
        assert items == []
        assert called is False
        engine.close()

    def test_extends_already_warmed_master_catalog(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        engine._master_catalog = [
            CatalogItem(product_id="old", drug_name="Old", source=SourceName.GIATHUOCTOT),
        ]

        import crawlers.engine as engine_mod
        monkeypatch.setattr(
            engine_mod, "append_manual_product",
            lambda items, canonical_name, path=None: "MP2",
        )

        engine.add_manual_product(
            {"giathuoctot": "https://www.giathuoctot.com/product/new-item"}, "New"
        )
        assert len(engine._master_catalog) == 2
        assert engine._master_catalog[-1].product_id == "new-item"
        engine.close()

    def test_does_not_extend_catalog_when_not_yet_warmed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        assert engine._master_catalog is None

        import crawlers.engine as engine_mod
        monkeypatch.setattr(
            engine_mod, "append_manual_product",
            lambda items, canonical_name, path=None: "MP2",
        )

        engine.add_manual_product(
            {"giathuoctot": "https://www.giathuoctot.com/product/new-item"}, "New"
        )
        assert engine._master_catalog is None
        engine.close()


class TestSetManualListing:
    def test_detected_url_writes_and_extends_catalog(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        engine._master_catalog = [
            CatalogItem(product_id="old", drug_name="Boganic", source=SourceName.GIATHUOCTOT,
                        master_product_id="MP1"),
        ]

        saved = {}

        def fake_append(master_id, item, canonical_name, path=None):
            saved["master_id"] = master_id
            saved["item"] = item

        import crawlers.engine as engine_mod
        monkeypatch.setattr(engine_mod, "append_or_update_listing", fake_append)

        result = engine.set_manual_listing(
            "MP1", "chothuoc247", "https://chothuoc247.vn/san-pham/12345", "Boganic"
        )
        assert result is not None
        assert result.product_id == "12345"
        assert result.source == SourceName.CHOTHUOC247
        assert result.master_product_id == "MP1"
        assert saved["master_id"] == "MP1"
        assert len(engine._master_catalog) == 2
        engine.close()

    def test_undetectable_url_returns_none_no_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)

        called = False

        def fake_append(master_id, item, canonical_name, path=None):
            nonlocal called
            called = True

        import crawlers.engine as engine_mod
        monkeypatch.setattr(engine_mod, "append_or_update_listing", fake_append)

        result = engine.set_manual_listing("MP1", "chothuoc247", "https://khong-hop-le.com", "X")
        assert result is None
        assert called is False
        engine.close()

    def test_replaces_old_item_same_master_and_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sửa URL cho 1 site đã có trong nhóm — item cũ (cùng master_product_id +
        source) phải bị thay bằng item mới, không giữ cả 2 (trùng site)."""
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        engine._master_catalog = [
            CatalogItem(product_id="old-id", drug_name="Boganic", source=SourceName.GIATHUOCTOT,
                        master_product_id="MP1"),
        ]

        import crawlers.engine as engine_mod
        monkeypatch.setattr(
            engine_mod, "append_or_update_listing", lambda *a, **kw: None,
        )

        engine.set_manual_listing(
            "MP1", "giathuoctot", "https://www.giathuoctot.com/product/new-slug", "Boganic"
        )
        assert len(engine._master_catalog) == 1
        assert engine._master_catalog[0].product_id == "new-slug"
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

    def test_multiple_items_same_site_share_one_login(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bug thật đã xảy ra: 1 nhóm gộp nhiều CatalogItem của CÙNG 1 site
        (sàn đa nhà cung cấp như thuocsi hay có vài SKU trùng cho cùng 1
        thuốc). Trước đây mỗi item tự tạo crawler + login riêng — chạy song
        song thì bắn hàng chục login ĐỒNG THỜI vào cùng 1 site, khiến site
        tưởng bị tấn công và tự khoá IP (HTTP 403 "đăng nhập quá nhiều lần").
        Giờ phải gom theo site, chỉ login 1 lần rồi tái sử dụng cho mọi item
        cùng site."""
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        login_count = 0
        instance_count = 0

        class _FakeCrawler:
            def __init__(self, *a, **kw):
                nonlocal instance_count
                instance_count += 1

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def crawl(self, keyword):
                nonlocal login_count
                login_count += 1  # giả lập: mỗi lần login thật sẽ tăng số này
                return [_dp(keyword, price=1000)]

        from crawlers import engine as engine_mod

        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", _FakeCrawler)
        items = [
            CatalogItem(product_id="p1", drug_name="Boganic A", source=SourceName.GIATHUOCTOT),
            CatalogItem(product_id="p2", drug_name="Boganic B", source=SourceName.GIATHUOCTOT),
            CatalogItem(product_id="p3", drug_name="Boganic C", source=SourceName.GIATHUOCTOT),
        ]
        results = asyncio.run(engine.fetch_live_prices(items))

        assert instance_count == 1, "chỉ được tạo 1 crawler instance (1 login) cho cả site, không phải 1/item"
        assert login_count == 3, "vẫn phải search đủ 3 lần (3 tên khác nhau), chỉ login chung 1 lần"
        assert len(results) == 3
        engine.close()


class TestCheckLogins:
    """`check_logins` thử đăng nhập từng site enabled để cảnh báo NGAY lúc mở app
    (vd thuocsisaigon sai tài khoản → giá bị ẩn = 0), thay vì crawl xong mới biết."""

    def _cfg_two_enabled(self, path: Path) -> None:
        path.write_text(
            """
defaults:
  rate_limit:
    delay_seconds: 0
    max_retries: 1
    retry_backoff_seconds: 0
sites:
  giathuoctot:
    name: "Gia Thuoc Tot"
    base_url: "https://x"
    credentials: {username: "u", password: "p"}
  thuocsisaigon:
    name: "Thuoc Si Sai Gon"
    base_url: "https://y"
    credentials: {username: "u", password: "p"}
""",
            encoding="utf-8",
        )

    def _fake(self, ok_sites: set[str]):
        class _FakeCrawler:
            def __init__(self, config, log=None, **kwargs):
                self._cfg = config
                self._log = log or (lambda _m: None)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return None

            async def ensure_auth(self, *, force_real_login: bool = False):
                if self._cfg.id not in ok_sites:
                    raise RuntimeError(f"{self._cfg.name}: Thông tin đăng nhập không hợp lệ.")

        return _FakeCrawler

    def test_all_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        self._cfg_two_enabled(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        from crawlers import engine as engine_mod

        fake = self._fake({"giathuoctot", "thuocsisaigon"})
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", fake)
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "thuocsisaigon", fake)
        results = asyncio.run(engine.check_logins())
        assert {r.site_id for r in results} == {"giathuoctot", "thuocsisaigon"}
        assert all(r.ok and r.error == "" for r in results)
        engine.close()

    def test_one_fails_others_ok(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = tmp_path / "accounts.yaml"
        self._cfg_two_enabled(cfg)
        engine = CrawlerEngine(config_path=cfg, cache_db=tmp_path / "c.db", use_cache=False)
        from crawlers import engine as engine_mod

        fake = self._fake({"giathuoctot"})  # thuocsisaigon fail
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "giathuoctot", fake)
        monkeypatch.setitem(engine_mod.CRAWLER_REGISTRY, "thuocsisaigon", fake)
        by_id = {r.site_id: r for r in asyncio.run(engine.check_logins())}
        assert by_id["giathuoctot"].ok is True
        assert by_id["thuocsisaigon"].ok is False
        assert "không hợp lệ" in by_id["thuocsisaigon"].error
        engine.close()

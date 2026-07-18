"""Tests cho crawlers.watchlist_scheduler — run_watchlist_cycle, main --once."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from crawlers import watchlist_scheduler as wls
from crawlers.cache_manager import CacheManager
from utils.models import DrugPrice, SourceName


def _dp(name: str, price: int, source: SourceName = SourceName.GIATHUOCTOT) -> DrugPrice:
    return DrugPrice(drug_name=name, price_vnd=price, source=source)


class _FakeEngine:
    def __init__(self, results: list[DrugPrice], cache: CacheManager):
        self.results = results
        self.cache = cache
        self.refresh_called = False

    async def refresh_watchlist_prices(self) -> int:
        self.refresh_called = True
        self.cache.record_history(self.results)
        return len(self.results)

    def close(self) -> None:
        self.cache.close()


class TestRunWatchlistCycle:
    def test_first_cycle_no_alerts(self, tmp_path: Path) -> None:
        cache = CacheManager(tmp_path / "c.db")
        engine = _FakeEngine([_dp("A", 1000)], cache)
        logs: list[str] = []
        updated = asyncio.run(wls.run_watchlist_cycle(engine, log=logs.append))
        assert updated == 1
        assert engine.refresh_called
        assert any("Không có thay đổi" in m for m in logs)
        engine.close()

    def test_price_change_alert(self, tmp_path: Path) -> None:
        cache = CacheManager(tmp_path / "c.db")
        cache.record_history([_dp("A", 1000)])
        cache._conn.execute("UPDATE price_history SET recorded_at = recorded_at - 3600")
        cache._conn.commit()

        engine = _FakeEngine([_dp("A", 1200)], cache)
        logs: list[str] = []
        asyncio.run(wls.run_watchlist_cycle(engine, log=logs.append))
        assert any("GIÁ ĐỔI" in m for m in logs)
        engine.close()

    def test_empty_watchlist(self, tmp_path: Path) -> None:
        cache = CacheManager(tmp_path / "c.db")

        class _EmptyEngine(_FakeEngine):
            async def refresh_watchlist_prices(self) -> int:
                return 0

        engine = _EmptyEngine([], cache)
        updated = asyncio.run(wls.run_watchlist_cycle(engine, log=lambda _: None))
        assert updated == 0
        engine.close()


class TestChangePct:
    def test_pct(self) -> None:
        assert wls._change_pct(1000, 1200) == pytest.approx(20.0)
        assert wls._change_pct(0, 500) == 0.0
        assert wls._change_pct(2000, 1000) == pytest.approx(-50.0)


class TestParseArgs:
    def test_defaults(self) -> None:
        ns = wls._parse_args([])
        assert ns.interval is None
        assert ns.once is False

    def test_once(self) -> None:
        ns = wls._parse_args(["--once"])
        assert ns.once is True

    def test_interval(self) -> None:
        ns = wls._parse_args(["--interval", "5"])
        assert ns.interval == 5.0

    def test_config(self) -> None:
        ns = wls._parse_args(["--config", "/tmp/x.yaml"])
        assert ns.config == "/tmp/x.yaml"


class TestMain:
    def test_once_runs_single_cycle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        created: dict[str, Any] = {}

        class _EngineFactory:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                cache = CacheManager(tmp_path / "c.db")
                self._inner = _FakeEngine([_dp("A", 1000)], cache)
                from utils.models import WatchlistConfig
                self.watchlist_config = WatchlistConfig()
                created["engine"] = self

            async def refresh_watchlist_prices(self) -> int:
                return await self._inner.refresh_watchlist_prices()

            @property
            def cache(self):
                return self._inner.cache

            def close(self) -> None:
                self._inner.close()
                created["closed"] = True

        monkeypatch.setattr(wls, "CrawlerEngine", _EngineFactory)
        rc = wls.main(["--once"])
        assert rc == 0
        assert created.get("closed") is True

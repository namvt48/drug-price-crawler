"""Tests cho crawlers.scheduler — run_cycle, cảnh báo giá đổi, main --once."""

from __future__ import annotations

import asyncio
import csv
import time
from pathlib import Path
from typing import Any

import pytest

from crawlers import scheduler
from crawlers.cache_manager import CacheManager
from utils.models import DrugPrice, SourceName


def _dp(name: str, price: int, source: SourceName = SourceName.GIATHUOCTOT) -> DrugPrice:
    return DrugPrice(drug_name=name, price_vnd=price, source=source)


class _FakeEngine:
    """Engine giả: crawl trả kết quả định sẵn + cache SQLite thật để có history."""

    def __init__(self, results: list[DrugPrice], cache: CacheManager):
        self.results = results
        self.cache = cache
        self.crawl_kwargs: dict[str, Any] = {}

    async def crawl(self, keyword: str, site_ids=None, force_refresh=False, progress=None):
        self.crawl_kwargs = {
            "keyword": keyword, "site_ids": site_ids, "force_refresh": force_refresh
        }
        self.cache.set("s", keyword or "all", self.results, ttl_hours=24)
        return self.results

    def close(self) -> None:
        self.cache.close()


class TestRunCycle:
    def test_first_cycle_no_alerts(self, tmp_path: Path) -> None:
        cache = CacheManager(tmp_path / "c.db")
        engine = _FakeEngine([_dp("A", 1000)], cache)
        logs: list[str] = []
        changes = asyncio.run(
            scheduler.run_cycle(
                engine, "kw", tmp_path / "p.csv", tmp_path / "alerts.csv", log=logs.append
            )
        )
        assert changes == []
        assert engine.crawl_kwargs["force_refresh"] is True
        assert (tmp_path / "p.csv").exists()
        assert not (tmp_path / "alerts.csv").exists()
        engine.close()

    def test_price_change_creates_alert(self, tmp_path: Path) -> None:
        cache = CacheManager(tmp_path / "c.db")
        # Vòng trước: giá 1000 (ghi history trực tiếp qua record_history).
        cache.record_history([_dp("A", 1000)])
        # Lùi mốc thời gian để bản ghi cũ không lọt vào cửa sổ recent_changes.
        cache._conn.execute("UPDATE price_history SET recorded_at = recorded_at - 3600")
        cache._conn.commit()

        engine = _FakeEngine([_dp("A", 1200)], cache)
        alerts = tmp_path / "alerts.csv"
        logs: list[str] = []
        changes = asyncio.run(
            scheduler.run_cycle(engine, "kw", tmp_path / "p.csv", alerts, log=logs.append)
        )
        assert len(changes) == 1
        assert changes[0]["prev_price_vnd"] == 1000
        assert changes[0]["price_vnd"] == 1200
        assert any("GIÁ ĐỔI" in m for m in logs)

        with alerts.open(encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
        assert rows[0]["old_price_vnd"] == "1000"
        assert rows[0]["new_price_vnd"] == "1200"
        assert rows[0]["change_pct"] == "20.0"
        engine.close()

    def test_same_price_no_alert(self, tmp_path: Path) -> None:
        cache = CacheManager(tmp_path / "c.db")
        cache.record_history([_dp("A", 1000)])
        engine = _FakeEngine([_dp("A", 1000)], cache)
        changes = asyncio.run(
            scheduler.run_cycle(
                engine, "kw", tmp_path / "p.csv", tmp_path / "a.csv", log=lambda _m: None
            )
        )
        assert changes == []
        engine.close()

    def test_empty_results_logged(self, tmp_path: Path) -> None:
        cache = CacheManager(tmp_path / "c.db")

        class _EmptyEngine(_FakeEngine):
            async def crawl(self, keyword, site_ids=None, force_refresh=False, progress=None):
                return []

        engine = _EmptyEngine([], cache)
        logs: list[str] = []
        asyncio.run(
            scheduler.run_cycle(engine, "kw", tmp_path / "p.csv", tmp_path / "a.csv", log=logs.append)
        )
        assert any("không có kết quả" in m.lower() for m in logs)
        assert not (tmp_path / "p.csv").exists()
        engine.close()

    def test_alerts_append_only_one_header(self, tmp_path: Path) -> None:
        alerts = tmp_path / "a.csv"
        change = {
            "drug_name": "A", "source": "S",
            "prev_price_vnd": 1, "price_vnd": 2, "recorded_at": time.time(),
        }
        scheduler._append_alerts(alerts, [change])
        scheduler._append_alerts(alerts, [change])
        lines = alerts.read_text(encoding="utf-8-sig").strip().splitlines()
        assert lines[0].startswith("detected_at")
        assert len(lines) == 3  # 1 header + 2 dòng dữ liệu


class TestChangePct:
    def test_pct(self) -> None:
        assert scheduler._change_pct(1000, 1200) == pytest.approx(20.0)
        assert scheduler._change_pct(0, 500) == 0.0


class TestMain:
    def test_once_runs_single_cycle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        created: dict[str, Any] = {}

        class _EngineFactory:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                cache = CacheManager(tmp_path / "c.db")
                self._inner = _FakeEngine([_dp("A", 1000)], cache)
                created["engine"] = self

            async def crawl(self, *a: Any, **kw: Any):
                return await self._inner.crawl(*a, **kw)

            @property
            def cache(self):
                return self._inner.cache

            def close(self) -> None:
                self._inner.close()
                created["closed"] = True

        monkeypatch.setattr(scheduler, "CrawlerEngine", _EngineFactory)
        rc = scheduler.main([
            "-k", "kw", "--once",
            "-o", str(tmp_path / "p.csv"),
            "--alerts", str(tmp_path / "a.csv"),
        ])
        assert rc == 0
        assert created.get("closed") is True
        assert (tmp_path / "p.csv").exists()

    def test_parse_args_defaults(self) -> None:
        ns = scheduler._parse_args(["-k", "x"])
        assert ns.interval_hours == 24.0
        assert ns.once is False
        assert ns.alerts == "output/price_alerts.csv"

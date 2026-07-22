"""Tests cho cli.main — monkeypatch CrawlerEngine + TrialManager."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

import cli
from utils.models import DrugPrice, SiteConfig, SourceName
from utils.trial_manager import TrialStatus


def _dp(name: str = "Test", price: int = 1000) -> DrugPrice:
    return DrugPrice(drug_name=name, price_vnd=price, source=SourceName.GIATHUOCTOT)


class _FakeEngine:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._results: list[DrugPrice] = kwargs.get("_results", [])
        self.crawl_called = False

    def available_sites(self) -> list[SiteConfig]:
        s = SiteConfig(id="giathuoctot", name="Gia Thuoc Tot", base_url="https://gtt.com")
        return [s]

    async def crawl(
        self,
        keyword: str,
        site_ids: list[str] | None = None,
        progress=None,
    ) -> list[DrugPrice]:
        self.crawl_called = True
        if progress:
            progress(1, 1)
        return self._results

    def suggest_names(self, prefix: str, limit: int = 30) -> list[str]:
        return []

    def find_by_name(self, name: str) -> list[DrugPrice]:
        return []

    def close(self) -> None:
        pass


class TestListSites:
    def test_list_sites_returns_0(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(cli, "CrawlerEngine", _FakeEngine)
        rc = cli.main(["--list-sites"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "giathuoctot" in out

    def test_list_sites_no_trial_check(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        trial_called = False

        class BadTrial:
            def check(self) -> TrialStatus:
                nonlocal trial_called
                trial_called = True
                return TrialStatus(False, 0, False, "expired")

        monkeypatch.setattr(cli, "CrawlerEngine", _FakeEngine)
        monkeypatch.setattr(cli, "TrialManager", BadTrial)
        rc = cli.main(["--list-sites"])
        assert rc == 0
        assert not trial_called


class TestCrawlCli:
    def test_no_results_returns_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("DPC_DEV", "1")
        monkeypatch.setattr(cli, "CrawlerEngine", _FakeEngine)
        rc = cli.main(["-k", "boganic", "-s", "giathuoctot"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "Không có kết quả" in out

    def test_with_results_writes_csv(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DPC_DEV", "1")

        class _EngineWithResults(_FakeEngine):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._results = [_dp("Boganic", 25000)]

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineWithResults)
        out_file = tmp_path / "prices.csv"
        rc = cli.main(["-k", "boganic", "-s", "giathuoctot", "-o", str(out_file)])
        assert rc == 0
        assert out_file.exists()
        out = capsys.readouterr().out
        assert "Đã ghi" in out

    def test_trial_expired_returns_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("DPC_DEV", raising=False)

        class ExpiredTrial:
            def check(self) -> TrialStatus:
                return TrialStatus(False, 0, False, "Hết hạn dùng thử.")

        monkeypatch.setattr(cli, "TrialManager", ExpiredTrial)
        monkeypatch.setattr(cli, "CrawlerEngine", _FakeEngine)
        rc = cli.main(["-k", "boganic"])
        assert rc == 2
        out = capsys.readouterr().out
        assert "Hết hạn" in out

    def test_no_cache_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DPC_DEV", "1")
        captured: dict[str, bool] = {}

        class _EngineNoCache(_FakeEngine):
            def __init__(self, *args, **kwargs):
                captured["use_cache"] = kwargs.get("use_cache", True)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineNoCache)
        cli.main(["-k", "kw", "--no-cache"])
        assert captured["use_cache"] is False

    def test_progress_output(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("DPC_DEV", "1")
        monkeypatch.setattr(cli, "CrawlerEngine", _FakeEngine)
        cli.main(["-k", "kw", "-s", "giathuoctot"])
        out = capsys.readouterr().out
        assert "tiến độ" in out

    def test_dump_groups_writes_yaml(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv("DPC_DEV", "1")

        class _EngineWithResults(_FakeEngine):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._results = [
                    _dp("Boganic Nén Bao Đường Traphaco (H/100V)", 67000),
                    _dp("Boganic bao duong H/5 vi x 20v Traphaco", 65000),
                    _dp("Boganic siro lọ 100ml Traphaco", 90000),
                ]

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineWithResults)
        groups_file = tmp_path / "groups.yaml"
        csv_file = tmp_path / "prices.csv"
        rc = cli.main([
            "-k", "boganic", "-s", "giathuoctot",
            "--dump-groups", str(groups_file),
            "-o", str(csv_file),
        ])
        assert rc == 0
        assert groups_file.exists()
        groups = yaml.safe_load(groups_file.read_text(encoding="utf-8"))
        assert isinstance(groups, dict) and len(groups) > 0
        found_pair = False
        for members in groups.values():
            assert isinstance(members, list)
            if "Boganic Nén Bao Đường Traphaco (H/100V)" in members:
                assert "Boganic bao duong H/5 vi x 20v Traphaco" in members
                found_pair = True
        assert found_pair
        assert csv_file.exists()
        out = capsys.readouterr().out
        assert "nhóm" in out


class TestHistoryCli:
    def test_history_prints_rows(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("DPC_DEV", "1")

        class _EngineWithHistory(_FakeEngine):
            def get_history(self, name: str) -> list[dict]:
                return [
                    {"drug_name": name, "source": "Giathuoctot", "price_vnd": 1000, "recorded_at": 1_700_000_000.0},
                    {"drug_name": name, "source": "Giathuoctot", "price_vnd": 1200, "recorded_at": 1_700_100_000.0},
                ]

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineWithHistory)
        rc = cli.main(["--history", "Boganic"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Lịch sử giá 'Boganic'" in out
        assert "1,000đ" in out
        assert "1,200đ" in out

    def test_history_empty_returns_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("DPC_DEV", "1")

        class _EngineNoHistory(_FakeEngine):
            def get_history(self, name: str) -> list[dict]:
                return []

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineNoHistory)
        rc = cli.main(["--history", "Unknown"])
        assert rc == 1
        assert "Chưa có lịch sử giá" in capsys.readouterr().out


class TestFilterFlags:
    def test_flags_override_engine_filters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DPC_DEV", "1")
        from utils.models import FilterConfig

        class _EngineWithFilters(_FakeEngine):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.filters = FilterConfig()

        holder: dict = {}

        class _Captured(_EngineWithFilters):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                holder["engine"] = self

        monkeypatch.setattr(cli, "CrawlerEngine", _Captured)
        cli.main([
            "-k", "kw",
            "--manufacturer", "Traphaco", "--manufacturer", "Sanofi",
            "--contains", "boganic",
        ])
        eng = holder["engine"]
        assert eng.filters.manufacturers == ["Traphaco", "Sanofi"]
        assert eng.filters.name_keywords == ["boganic"]


class TestExcelOut:
    def test_xlsx_extension_writes_excel(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("DPC_DEV", "1")

        class _EngineWithResults(_FakeEngine):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._results = [_dp("Boganic", 25000)]

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineWithResults)
        out_file = tmp_path / "prices.xlsx"
        rc = cli.main(["-k", "boganic", "-o", str(out_file)])
        assert rc == 0
        from openpyxl import load_workbook

        wb = load_workbook(out_file)
        assert wb.active["A2"].value == "Boganic"
        wb.close()


class TestParseArgs:
    def test_keyword(self) -> None:
        ns = cli._parse_args(["-k", "boganic"])
        assert ns.keyword == "boganic"

    def test_sites(self) -> None:
        ns = cli._parse_args(["-s", "a,b,c"])
        assert ns.sites == "a,b,c"

    def test_no_cache(self) -> None:
        ns = cli._parse_args(["--no-cache"])
        assert ns.no_cache is True

    def test_list_sites(self) -> None:
        ns = cli._parse_args(["--list-sites"])
        assert ns.list_sites is True

    def test_out_default(self) -> None:
        ns = cli._parse_args([])
        assert ns.out == "output/prices.csv"

    def test_config(self) -> None:
        ns = cli._parse_args(["--config", "/tmp/x.yaml"])
        assert ns.config == "/tmp/x.yaml"


class TestWatchlistCli:

    def test_watchlist_empty(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("DPC_DEV", "1")

        class _EngineEmptyWatchlist(_FakeEngine):
            def get_watchlist(self):
                return []

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineEmptyWatchlist)
        rc = cli.main(["--watchlist"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "trống" in out.lower()

    def test_watchlist_with_items(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("DPC_DEV", "1")
        from utils.models import WatchlistItem
        import time

        class _EngineWithWatchlist(_FakeEngine):
            def get_watchlist(self):
                return [
                    WatchlistItem(
                        site_id="giathuoctot",
                        product_id="p1",
                        source=SourceName.GIATHUOCTOT,
                        drug_name="Boganic",
                        search_name="boganic",
                        added_at=time.time(),
                        last_price_vnd=67000,
                        last_checked=time.time(),
                    ),
                ]

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineWithWatchlist)
        rc = cli.main(["--watchlist"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Boganic" in out
        assert "67,000đ" in out

    def test_add_watchlist_success(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("DPC_DEV", "1")
        from utils.models import CatalogItem

        class _EngineAddWatchlist(_FakeEngine):
            def suggest_catalog(self, query, limit=30):
                return [CatalogItem(product_id="p1", drug_name="Boganic", source=SourceName.GIATHUOCTOT)]

            def add_to_watchlist(self, ci):
                pass

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineAddWatchlist)
        rc = cli.main(["--add-watchlist", "boga"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Đã thêm" in out
        assert "Boganic" in out

    def test_add_watchlist_no_results(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("DPC_DEV", "1")

        class _EngineNoCatalog(_FakeEngine):
            def suggest_catalog(self, query, limit=30):
                return []

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineNoCatalog)
        rc = cli.main(["--add-watchlist", "zzz"])
        assert rc == 1
        out = capsys.readouterr().out
        assert "Không tìm thấy" in out

    def test_refresh_watchlist(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        monkeypatch.setenv("DPC_DEV", "1")

        class _EngineRefresh(_FakeEngine):
            async def refresh_watchlist_prices(self) -> int:
                return 3

        monkeypatch.setattr(cli, "CrawlerEngine", _EngineRefresh)
        rc = cli.main(["--refresh-watchlist"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "3 mục" in out


class TestParseArgsWatchlist:
    def test_watchlist(self) -> None:
        ns = cli._parse_args(["--watchlist"])
        assert ns.watchlist is True

    def test_add_watchlist(self) -> None:
        ns = cli._parse_args(["--add-watchlist", "boganic"])
        assert ns.add_watchlist == "boganic"

    def test_refresh_watchlist(self) -> None:
        ns = cli._parse_args(["--refresh-watchlist"])
        assert ns.refresh_watchlist is True

"""Tests cho utils.filters + FilterConfig + config_loader.load_filters."""

from __future__ import annotations

from pathlib import Path

from utils.config_loader import load_filters
from utils.filters import apply_filters, matches
from utils.models import DrugPrice, FilterConfig, SourceName


def _dp(name: str, maker: str = "", price: int = 1000) -> DrugPrice:
    return DrugPrice(
        drug_name=name, manufacturer=maker, price_vnd=price, source=SourceName.GIATHUOCTOT
    )


class TestFilterConfig:
    def test_inactive_by_default(self) -> None:
        assert FilterConfig().is_active() is False

    def test_active_with_any_criterion(self) -> None:
        assert FilterConfig(name_keywords=["a"]).is_active()
        assert FilterConfig(manufacturers=["a"]).is_active()
        assert FilterConfig(min_price_vnd=1).is_active()
        assert FilterConfig(max_price_vnd=1).is_active()


class TestMatches:
    def test_name_keyword_accent_insensitive(self) -> None:
        # strip_accents đưa "bổ gan"/"bô gan"/"BO GAN" đều về "bo gan" → match hết.
        cfg = FilterConfig(name_keywords=["bổ gan"])
        assert matches(_dp("Thuốc bổ gan Boganic"), cfg) is True
        assert matches(_dp("Thuoc BO GAN Boganic"), cfg) is True

    def test_name_keyword_no_match(self) -> None:
        cfg = FilterConfig(name_keywords=["paracetamol"])
        assert matches(_dp("Boganic"), cfg) is False

    def test_manufacturer_partial_case_insensitive(self) -> None:
        cfg = FilterConfig(manufacturers=["traphaco"])
        assert matches(_dp("X", maker="Công ty Traphaco JSC"), cfg) is True
        assert matches(_dp("X", maker="Sanofi"), cfg) is False

    def test_price_range(self) -> None:
        cfg = FilterConfig(min_price_vnd=1000, max_price_vnd=2000)
        assert matches(_dp("X", price=1500), cfg) is True
        assert matches(_dp("X", price=999), cfg) is False
        assert matches(_dp("X", price=2001), cfg) is False

    def test_zero_limits_mean_unbounded(self) -> None:
        cfg = FilterConfig(name_keywords=["x"])
        assert matches(_dp("X thuoc", price=999_999_999), cfg) is True


class TestApplyFilters:
    def test_none_or_inactive_passthrough(self) -> None:
        prices = [_dp("A"), _dp("B")]
        assert apply_filters(prices, None) == prices
        assert apply_filters(prices, FilterConfig()) == prices

    def test_filters_combine_and(self) -> None:
        cfg = FilterConfig(name_keywords=["boganic"], manufacturers=["traphaco"])
        prices = [
            _dp("Boganic vien", maker="Traphaco"),
            _dp("Boganic vien", maker="Sanofi"),
            _dp("Panadol", maker="Traphaco"),
        ]
        kept = apply_filters(prices, cfg)
        assert len(kept) == 1
        assert kept[0].manufacturer == "Traphaco"


class TestLoadFilters:
    def test_missing_file_returns_inactive(self, tmp_path: Path) -> None:
        cfg = load_filters(tmp_path / "nope.yaml")
        assert cfg.is_active() is False

    def test_missing_block_returns_inactive(self, tmp_path: Path) -> None:
        p = tmp_path / "a.yaml"
        p.write_text("sites: {}\n", encoding="utf-8")
        assert load_filters(p).is_active() is False

    def test_loads_block(self, tmp_path: Path) -> None:
        p = tmp_path / "a.yaml"
        p.write_text(
            "filters:\n  name_keywords: [boganic]\n  manufacturers: [Traphaco]\n"
            "  min_price_vnd: 100\n  max_price_vnd: 90000\n",
            encoding="utf-8",
        )
        cfg = load_filters(p)
        assert cfg.name_keywords == ["boganic"]
        assert cfg.manufacturers == ["Traphaco"]
        assert cfg.min_price_vnd == 100
        assert cfg.max_price_vnd == 90000

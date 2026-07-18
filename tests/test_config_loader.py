"""Tests cho utils.config_loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.config_loader import (
    _deep_merge,
    app_base_dir,
    default_config_path,
    load_sites,
    load_watchlist_config,
    update_credentials,
)
from utils.models import SiteConfig, WatchlistConfig


def _write_config(path: Path) -> None:
    path.write_text(
        """
defaults:
  cache:
    enabled: true
    ttl_hours: 24
  auth:
    expiry_hours: 12
    retry_on_401: true
    max_auth_retries: 3
  rate_limit:
    delay_seconds: 2
    max_retries: 3
    retry_backoff_seconds: 5
  user_agent: "DefaultUA/1.0"

sites:
  giathuoctot:
    name: "Gia Thuoc Tot"
    base_url: "https://www.giathuoctot.com"
    credentials:
      username: "user1"
      password: "pass1"
    cache:
      ttl_hours: 48
  chothuoc247:
    enabled: false
    name: "Cho Thuoc 247"
""",
        encoding="utf-8",
    )


class TestLoadSites:
    def test_load_two_sites(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        sites = load_sites(cfg)
        assert "giathuoctot" in sites
        assert "chothuoc247" in sites

    def test_deep_merge_override(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        sites = load_sites(cfg)
        gtt = sites["giathuoctot"]
        # ttl overridden to 48.
        assert gtt.cache.ttl_hours == 48
        # enabled inherited from defaults.
        assert gtt.cache.enabled is True
        # auth inherited from defaults.
        assert gtt.auth.expiry_hours == 12
        assert gtt.auth.retry_on_401 is True
        # rate_limit inherited.
        assert gtt.rate_limit.delay_seconds == 2
        # user_agent inherited from defaults.
        assert gtt.user_agent == "DefaultUA/1.0"
        # credentials from site.
        assert gtt.credentials.username == "user1"
        assert gtt.credentials.password == "pass1"
        assert gtt.name == "Gia Thuoc Tot"

    def test_disabled_site(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        sites = load_sites(cfg)
        assert sites["chothuoc247"].enabled is False

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_sites(tmp_path / "nonexistent.yaml")

    def test_returns_siteconfig_objects(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        sites = load_sites(cfg)
        assert isinstance(sites["giathuoctot"], SiteConfig)

    def test_empty_sites(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text("defaults:\n  cache:\n    enabled: true\nsites: {}\n", encoding="utf-8")
        sites = load_sites(cfg)
        assert sites == {}

    def test_site_without_defaults(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text(
            "sites:\n  foo:\n    name: Foo\n    base_url: https://foo.com\n",
            encoding="utf-8",
        )
        sites = load_sites(cfg)
        assert sites["foo"].name == "Foo"
        assert sites["foo"].base_url == "https://foo.com"


class TestDeepMerge:
    def test_shallow_override(self) -> None:
        result = _deep_merge({"a": 1, "b": 2}, {"b": 3})
        assert result == {"a": 1, "b": 3}

    def test_nested_merge(self) -> None:
        result = _deep_merge(
            {"auth": {"expiry": 12, "retry": True}},
            {"auth": {"expiry": 24}},
        )
        assert result == {"auth": {"expiry": 24, "retry": True}}

    def test_add_new_key(self) -> None:
        result = _deep_merge({"a": 1}, {"b": 2})
        assert result == {"a": 1, "b": 2}

    def test_override_non_dict_with_dict(self) -> None:
        result = _deep_merge({"a": 1}, {"a": {"x": 1}})
        assert result == {"a": {"x": 1}}

    def test_override_dict_with_non_dict(self) -> None:
        result = _deep_merge({"a": {"x": 1}}, {"a": 5})
        assert result == {"a": 5}

    def test_deep_nested(self) -> None:
        result = _deep_merge(
            {"a": {"b": {"c": 1, "d": 2}}},
            {"a": {"b": {"c": 99}}},
        )
        assert result == {"a": {"b": {"c": 99, "d": 2}}}

    def test_empty_override(self) -> None:
        result = _deep_merge({"a": 1}, {})
        assert result == {"a": 1}


class TestAppBaseDir:
    def test_returns_path(self) -> None:
        d = app_base_dir()
        assert isinstance(d, Path)

    def test_default_config_path(self) -> None:
        p = default_config_path()
        assert isinstance(p, Path)
        assert p.name == "accounts.yaml"


class TestUpdateCredentials:
    def test_replace_existing_preserves_comments(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text(
            "# header comment\n"
            "sites:\n"
            "  giathuoctot:\n"
            "    name: \"Gia Thuoc Tot\"  # inline comment\n"
            "    credentials:\n"
            "      username: \"old_user\"\n"
            "      password: \"old_pass\"\n"
            "  chothuoc247:\n"
            "    credentials:\n"
            "      username: \"other\"\n"
            "      password: \"otherpass\"\n",
            encoding="utf-8",
        )
        update_credentials("giathuoctot", "new_user", "new_pass", cfg)

        text = cfg.read_text(encoding="utf-8")
        assert "# header comment" in text
        assert "# inline comment" in text

        sites = load_sites(cfg)
        assert sites["giathuoctot"].credentials.username == "new_user"
        assert sites["giathuoctot"].credentials.password == "new_pass"
        # Site khác không bị đụng.
        assert sites["chothuoc247"].credentials.username == "other"
        assert sites["chothuoc247"].credentials.password == "otherpass"

    def test_special_characters_round_trip(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text(
            "sites:\n"
            "  giathuoctot:\n"
            "    credentials:\n"
            "      username: \"u\"\n"
            "      password: \"p\"\n",
            encoding="utf-8",
        )
        tricky = 'a: b # c "quote" \\slash'
        update_credentials("giathuoctot", "0912#345", tricky, cfg)
        sites = load_sites(cfg)
        assert sites["giathuoctot"].credentials.username == "0912#345"
        assert sites["giathuoctot"].credentials.password == tricky

    def test_insert_when_credentials_block_missing(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text(
            "sites:\n"
            "  giathuoctot:\n"
            "    name: \"Gia Thuoc Tot\"\n"
            "    enabled: true\n",
            encoding="utf-8",
        )
        update_credentials("giathuoctot", "u1", "p1", cfg)
        sites = load_sites(cfg)
        assert sites["giathuoctot"].credentials.username == "u1"
        assert sites["giathuoctot"].credentials.password == "p1"
        assert sites["giathuoctot"].name == "Gia Thuoc Tot"
        assert sites["giathuoctot"].enabled is True

    def test_insert_missing_field_in_existing_block(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text(
            "sites:\n"
            "  giathuoctot:\n"
            "    credentials:\n"
            "      username: \"only_user\"\n",
            encoding="utf-8",
        )
        update_credentials("giathuoctot", "kept_user", "added_pass", cfg)
        sites = load_sites(cfg)
        assert sites["giathuoctot"].credentials.username == "kept_user"
        assert sites["giathuoctot"].credentials.password == "added_pass"

    def test_credentials_followed_by_sibling_with_comment(self, tmp_path: Path) -> None:
        # credentials có comment bên trong VÀ có key auth: đứng sau → cover nhánh
        # bỏ qua comment + kết thúc block credentials trước cuối site.
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text(
            "sites:\n"
            "  giathuoctot:\n"
            "    credentials:\n"
            "      # tài khoản chính\n"
            "      username: \"old\"\n"
            "      password: \"old\"\n"
            "    auth:\n"
            "      method: form_login\n",
            encoding="utf-8",
        )
        update_credentials("giathuoctot", "newu", "newp", cfg)
        text = cfg.read_text(encoding="utf-8")
        assert "# tài khoản chính" in text
        sites = load_sites(cfg)
        assert sites["giathuoctot"].credentials.username == "newu"
        assert sites["giathuoctot"].credentials.password == "newp"
        assert sites["giathuoctot"].auth.method == "form_login"

    def test_site_not_found_raises(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        _write_config(cfg)
        with pytest.raises(ValueError, match="nosuchsite"):
            update_credentials("nosuchsite", "u", "p", cfg)

    def test_missing_sites_block_raises(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text("filters: {}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="sites"):
            update_credentials("giathuoctot", "u", "p", cfg)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            update_credentials("giathuoctot", "u", "p", tmp_path / "nope.yaml")

    def test_does_not_match_nested_key_named_like_site(self, tmp_path: Path) -> None:
        # 'auth' xuất hiện như key lồng trong site khác — không được nhận nhầm là site.
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text(
            "sites:\n"
            "  giathuoctot:\n"
            "    auth:\n"
            "      method: form_login\n"
            "    credentials:\n"
            "      username: \"u\"\n"
            "      password: \"p\"\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="auth"):
            update_credentials("auth", "x", "y", cfg)


class TestLoadWatchlistConfig:
    def test_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        wc = load_watchlist_config(tmp_path / "nonexistent.yaml")
        assert isinstance(wc, WatchlistConfig)
        assert wc.refresh_interval_minutes == 10
        assert wc.catalog_ttl_hours == 720

    def test_missing_block_returns_defaults(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text("sites: {}\n", encoding="utf-8")
        wc = load_watchlist_config(cfg)
        assert wc.refresh_interval_minutes == 10
        assert wc.catalog_ttl_hours == 720

    def test_custom_values(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text(
            "watchlist:\n  refresh_interval_minutes: 5\n  catalog_ttl_hours: 72\n",
            encoding="utf-8",
        )
        wc = load_watchlist_config(cfg)
        assert wc.refresh_interval_minutes == 5
        assert wc.catalog_ttl_hours == 72

    def test_partial_block_uses_defaults(self, tmp_path: Path) -> None:
        cfg = tmp_path / "accounts.yaml"
        cfg.write_text("watchlist:\n  refresh_interval_minutes: 15\n", encoding="utf-8")
        wc = load_watchlist_config(cfg)
        assert wc.refresh_interval_minutes == 15
        assert wc.catalog_ttl_hours == 720

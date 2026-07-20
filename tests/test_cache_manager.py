"""Tests cho crawlers.cache_manager."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from crawlers.cache_manager import CacheManager
from utils.models import DrugPrice, SourceName, WatchlistItem


def _dp(name: str, source: SourceName = SourceName.GIATHUOCTOT, price: int = 1000) -> DrugPrice:
    return DrugPrice(
        drug_name=name,
        price_vnd=price,
        price_display=f"{price}đ",
        source=source,
        source_url="https://example.com",
    )


class TestCacheGetSet:
    def test_set_then_get(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        data = [_dp("A"), _dp("B", price=2000)]
        cm.set("giathuoctot", "boganic", data, ttl_hours=24)
        got = cm.get("giathuoctot", "boganic")
        assert got is not None
        assert len(got) == 2
        assert got[0].drug_name == "A"
        assert got[1].price_vnd == 2000
        cm.close()

    def test_get_miss(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        assert cm.get("unknown", "kw") is None
        cm.close()

    def test_overwrite_on_set(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A")], ttl_hours=1)
        cm.set("s", "k", [_dp("B"), _dp("C")], ttl_hours=1)
        got = cm.get("s", "k")
        assert got is not None
        assert len(got) == 2
        cm.close()


class TestCacheExpiry:
    def test_expired_returns_none(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A")], ttl_hours=1)
        # Insert with old crawled_at directly (epoch 8 days ago).
        cm._conn.execute(
            "UPDATE crawl_cache SET crawled_at = ? WHERE cache_key = ?",
            (time.time() - 8 * 3600, cm._key("s", "k")),
        )
        cm._conn.commit()
        assert cm.get("s", "k") is None
        cm.close()

    def test_is_expired_boundary(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        # crawled_at exactly ttl_hours ago -> expired (strict >).
        now = time.time()
        assert cm._is_expired(now - 3600, 1.0) is True
        assert cm._is_expired(now - 3500, 1.0) is False
        cm.close()


class TestAgeHours:
    def test_age_hours_hit(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A")], ttl_hours=24)
        age = cm.age_hours("s", "k")
        assert age is not None
        assert age >= 0
        assert age < 1
        cm.close()

    def test_age_hours_miss(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        assert cm.age_hours("nope", "k") is None
        cm.close()


class TestSuggestNames:
    def test_prefix_match(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s1", "k", [_dp("Paracetamol"), _dp("ParaExtra")], ttl_hours=24)
        cm.set("s2", "k2", [_dp("Panadol")], ttl_hours=24)
        names = cm.suggest_names("para")
        assert "Paracetamol" in names
        assert "ParaExtra" in names
        # "Panadol" does NOT contain substring "para" (p-a-n vs p-a-r).
        assert "Panadol" not in names
        cm.close()

    def test_empty_prefix_returns_all(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A"), _dp("B"), _dp("C")], ttl_hours=24)
        names = cm.suggest_names("")
        assert len(names) == 3
        cm.close()

    def test_limit(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        data = [_dp(f"N{i}") for i in range(50)]
        cm.set("s", "k", data, ttl_hours=24)
        names = cm.suggest_names("", limit=5)
        assert len(names) == 5
        cm.close()

    def test_excludes_expired(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("Fresh")], ttl_hours=24)
        cm.set("s2", "k2", [_dp("Stale")], ttl_hours=1)
        cm._conn.execute(
            "UPDATE crawl_cache SET crawled_at = ? WHERE cache_key = ?",
            (time.time() - 2 * 3600, cm._key("s2", "k2")),
        )
        cm._conn.commit()
        names = cm.suggest_names("")
        assert "Fresh" in names
        assert "Stale" not in names
        cm.close()


class TestFindByName:
    def test_exact_match_across_sources(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s1", "k", [_dp("Paracetamol", SourceName.GIATHUOCTOT, 1000)], ttl_hours=24)
        cm.set("s2", "k", [_dp("Paracetamol", SourceName.THUOCSI, 2000)], ttl_hours=24)
        results = cm.find_by_name("Paracetamol")
        assert len(results) == 2
        prices = sorted(r.price_vnd for r in results)
        assert prices == [1000, 2000]
        cm.close()

    def test_case_insensitive(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("Paracetamol")], ttl_hours=24)
        results = cm.find_by_name("paracetamol")
        assert len(results) == 1
        cm.close()

    def test_no_match(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A")], ttl_hours=24)
        assert cm.find_by_name("ZZZ") == []
        cm.close()

    def test_excludes_expired(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("Exp")], ttl_hours=1)
        cm._conn.execute(
            "UPDATE crawl_cache SET crawled_at = ? WHERE cache_key = ?",
            (time.time() - 2 * 3600, cm._key("s", "k")),
        )
        cm._conn.commit()
        assert cm.find_by_name("Exp") == []
        cm.close()


class TestClearAndClose:
    def test_clear(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A")], ttl_hours=24)
        cm.clear()
        assert cm.get("s", "k") is None
        cm.close()

    def test_close_idempotent(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.close()
        # No error on second close (sqlite3.Connection.close is idempotent).
        cm.close()

    def test_corrupt_payload_returns_none(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm._conn.execute(
            "INSERT INTO crawl_cache (cache_key, site_id, keyword, payload, crawled_at, ttl_hours) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cm._key("s", "k"), "s", "k", "NOT-JSON", time.time(), 24),
        )
        cm._conn.commit()
        assert cm.get("s", "k") is None
        cm.close()


class TestKey:
    def test_key_format(self) -> None:
        assert CacheManager._key("site", "Keyword") == "site:keyword"


class TestCatalogWatchlistTables:
    def test_watchlist_table_created(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cur = cm._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watchlist'"
        )
        assert cur.fetchone() is not None
        cm.close()

    def test_watchlist_index_created(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cur = cm._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_watchlist_site'"
        )
        assert cur.fetchone() is not None
        cm.close()

    def test_watchlist_pk_composite(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm._conn.execute(
            "INSERT INTO watchlist (site_id, source, product_id, drug_name, added_at) VALUES (?, ?, ?, ?, ?)",
            ("giathuoctot", "Giathuoctot", "p1", "A", time.time()),
        )
        cm._conn.commit()
        cm._conn.execute(
            "INSERT OR REPLACE INTO watchlist (site_id, source, product_id, drug_name, added_at) VALUES (?, ?, ?, ?, ?)",
            ("giathuoctot", "Giathuoctot", "p1", "B", time.time()),
        )
        cm._conn.commit()
        cur = cm._conn.execute("SELECT drug_name FROM watchlist WHERE site_id=? AND product_id=?",
                               ("giathuoctot", "p1"))
        assert cur.fetchone()[0] == "B"
        cm.close()


class TestPriceHistory:
    def test_set_records_history(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A", price=1000)], ttl_hours=24)
        rows = cm.get_history("A")
        assert len(rows) == 1
        assert rows[0]["price_vnd"] == 1000
        assert rows[0]["source"] == SourceName.GIATHUOCTOT.value
        cm.close()

    def test_same_price_not_duplicated(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A", price=1000)], ttl_hours=24)
        cm.set("s", "k", [_dp("A", price=1000)], ttl_hours=24)
        assert len(cm.get_history("A")) == 1
        cm.close()

    def test_price_change_appends(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A", price=1000)], ttl_hours=24)
        cm.set("s", "k", [_dp("A", price=1200)], ttl_hours=24)
        rows = cm.get_history("A")
        assert [r["price_vnd"] for r in rows] == [1000, 1200]
        cm.close()

    def test_zero_price_skipped(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        assert cm.record_history([_dp("Hidden", price=0)]) == 0
        assert cm.get_history("Hidden") == []
        cm.close()

    def test_history_per_source(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.record_history([_dp("A", SourceName.GIATHUOCTOT, 1000)])
        cm.record_history([_dp("A", SourceName.THUOCSI, 900)])
        rows = cm.get_history("a")  # case-insensitive
        assert len(rows) == 2
        cm.close()

    def test_recent_changes_window_and_diff(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.record_history([_dp("A", price=1000)])
        # Đẩy bản ghi đầu ra khỏi cửa sổ.
        cm._conn.execute("UPDATE price_history SET recorded_at = recorded_at - 7200")
        cm._conn.commit()
        cutoff = time.time() - 60
        cm.record_history([_dp("A", price=1300)])
        changes = cm.recent_changes(cutoff)
        assert len(changes) == 1
        assert changes[0]["prev_price_vnd"] == 1000
        assert changes[0]["price_vnd"] == 1300
        cm.close()

    def test_recent_changes_first_record_not_a_change(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.record_history([_dp("New", price=500)])
        assert cm.recent_changes(time.time() - 60) == []
        cm.close()


class TestCanonicalReadHelpers:
    def test_all_live_names_unique(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s1", "k", [_dp("A"), _dp("B")], ttl_hours=24)
        cm.set("s2", "k", [_dp("A", SourceName.THUOCSI)], ttl_hours=24)
        names = cm.all_live_names()
        assert sorted(names) == ["A", "B"]
        cm.close()

    def test_find_by_names_multi_variant(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s1", "k", [_dp("Boganic (H/100V)", price=1000)], ttl_hours=24)
        cm.set("s2", "k", [_dp("Boganic h/5v", SourceName.THUOCSI, 1100)], ttl_hours=24)
        cm.set("s3", "k", [_dp("Panadol", SourceName.THUOCSI, 500)], ttl_hours=24)
        records = cm.find_by_names(["Boganic (H/100V)", "boganic h/5v"])
        assert len(records) == 2
        assert all("Boganic" in r.drug_name for r in records)
        cm.close()

    def test_find_by_names_empty_input(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.set("s", "k", [_dp("A")], ttl_hours=24)
        assert cm.find_by_names([]) == []
        assert cm.find_by_names(["  "]) == []
        cm.close()


def _wi(
    site_id: str = "giathuoctot",
    product_id: str = "p1",
    source: SourceName = SourceName.GIATHUOCTOT,
    drug_name: str = "Boganic",
    search_name: str = "boganic",
    added_at: float = 0.0,
    last_price_vnd: int = 0,
    last_checked: float = 0.0,
    image_url: str = "",
) -> WatchlistItem:
    return WatchlistItem(
        site_id=site_id,
        product_id=product_id,
        source=source,
        drug_name=drug_name,
        search_name=search_name,
        added_at=added_at,
        last_price_vnd=last_price_vnd,
        last_checked=last_checked,
        image_url=image_url,
    )


class TestWatchlistAddRemove:
    def test_add_and_get(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.add_to_watchlist(_wi(added_at=time.time()))
        items = cm.get_watchlist()
        assert len(items) == 1
        assert items[0].drug_name == "Boganic"
        cm.close()

    def test_add_replaces_existing(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.add_to_watchlist(_wi(drug_name="Old", added_at=1.0))
        cm.add_to_watchlist(_wi(drug_name="New", added_at=2.0))
        items = cm.get_watchlist()
        assert len(items) == 1
        assert items[0].drug_name == "New"
        cm.close()

    def test_remove_returns_true(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.add_to_watchlist(_wi())
        assert cm.remove_from_watchlist("p1", "giathuoctot") is True
        assert cm.get_watchlist() == []
        cm.close()

    def test_remove_nonexistent_returns_false(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        assert cm.remove_from_watchlist("nope", "giathuoctot") is False
        cm.close()


class TestWatchlistBySource:
    def test_grouped_by_site_id(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.add_to_watchlist(_wi(site_id="giathuoctot", product_id="p1", added_at=1.0))
        cm.add_to_watchlist(_wi(site_id="giathuoctot", product_id="p2", drug_name="C", added_at=2.0))
        cm.add_to_watchlist(_wi(site_id="chothuoc247", product_id="p3",
                               source=SourceName.CHOTHUOC247, drug_name="D", added_at=3.0))
        grouped = cm.get_watchlist_by_source()
        assert len(grouped) == 2
        assert len(grouped["giathuoctot"]) == 2
        assert len(grouped["chothuoc247"]) == 1
        cm.close()

    def test_empty_watchlist(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        assert cm.get_watchlist_by_source() == {}
        cm.close()


class TestWatchlistUpdatePrice:
    def test_update_sets_price_and_checked(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.add_to_watchlist(_wi())
        now = time.time()
        cm.update_watchlist_price("p1", "giathuoctot", 67000, now)
        items = cm.get_watchlist()
        assert items[0].last_price_vnd == 67000
        assert items[0].last_checked == now
        cm.close()

    def test_update_nonexistent_silently_succeeds(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.update_watchlist_price("nope", "giathuoctot", 1000, time.time())
        assert cm.get_watchlist() == []
        cm.close()


class TestImageUrlPersistence:
    def test_watchlist_image_url_survives_add_get(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.add_to_watchlist(_wi(image_url="https://img.test/watch.jpg"))
        items = cm.get_watchlist()
        assert len(items) == 1
        assert items[0].image_url == "https://img.test/watch.jpg"
        cm.close()

    def test_watchlist_image_url_in_by_source(self, tmp_path: Path) -> None:
        cm = CacheManager(tmp_path / "c.db")
        cm.add_to_watchlist(_wi(image_url="https://img.test/src.jpg"))
        grouped = cm.get_watchlist_by_source()
        assert grouped["giathuoctot"][0].image_url == "https://img.test/src.jpg"
        cm.close()

    def test_db_migration_adds_image_url_to_old_db(self, tmp_path: Path) -> None:
        """Old DB without image_url column gets migrated on open."""
        db = tmp_path / "old.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE watchlist (site_id TEXT, source TEXT, product_id TEXT, drug_name TEXT,"
            " search_name TEXT, added_at REAL, last_price_vnd INTEGER, last_checked REAL,"
            " PRIMARY KEY (site_id, product_id))"
        )
        conn.commit()
        conn.close()

        cm = CacheManager(db)
        cols_wl = {r[1] for r in cm._conn.execute("PRAGMA table_info(watchlist)").fetchall()}
        assert "image_url" in cols_wl
        cm.close()

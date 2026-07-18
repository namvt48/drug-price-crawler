"""Cache kết quả crawl bằng SQLite, TTL theo từng site.

Ví von: như tủ lạnh có hạn sử dụng — mỗi hộp (site + keyword) dán ngày, quá
hạn (TTL) thì bỏ đi nấu mẻ mới. Tránh gọi lại 9 site mỗi lần search.

Dùng WAL mode cho bền (theo chuẩn dự án). Value lưu JSON list[DrugPrice].
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from utils.models import CatalogItem, DrugPrice, SourceName, WatchlistItem


class CacheManager:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crawl_cache (
                cache_key   TEXT PRIMARY KEY,
                site_id     TEXT NOT NULL,
                keyword     TEXT NOT NULL,
                payload     TEXT NOT NULL,   -- JSON list[DrugPrice]
                crawled_at  REAL NOT NULL,   -- epoch seconds
                ttl_hours   REAL NOT NULL
            )
            """
        )
        # Lịch sử giá: chỉ ghi khi giá đổi so với lần ghi gần nhất (cùng thuốc + nguồn),
        # nên bảng không phình theo số lần crawl mà theo số lần giá thực sự thay đổi.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                drug_name   TEXT NOT NULL,
                source      TEXT NOT NULL,
                price_vnd   INTEGER NOT NULL,
                recorded_at REAL NOT NULL     -- epoch seconds
            )
            """
        )
        # Catalog: danh mục sản phẩm (id + tên, KHÔNG có giá) — crawl 1 lần, TTL dài.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog (
                source       TEXT NOT NULL,
                product_id   TEXT NOT NULL,
                drug_name    TEXT NOT NULL,
                search_name  TEXT NOT NULL DEFAULT '',
                manufacturer TEXT NOT NULL DEFAULT '',
                source_url   TEXT NOT NULL DEFAULT '',
                image_url    TEXT NOT NULL DEFAULT '',
                cached_at    REAL NOT NULL,
                PRIMARY KEY (source, product_id)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalog_search ON catalog (search_name)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_catalog_name ON catalog (drug_name)"
        )
        # Watchlist: sản phẩm user chọn theo dõi giá — refresh định kỳ.
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                site_id         TEXT NOT NULL,
                source          TEXT NOT NULL,
                product_id      TEXT NOT NULL,
                drug_name       TEXT NOT NULL,
                search_name     TEXT NOT NULL DEFAULT '',
                image_url       TEXT NOT NULL DEFAULT '',
                added_at        REAL NOT NULL,
                last_price_vnd  INTEGER NOT NULL DEFAULT 0,
                last_checked    REAL NOT NULL DEFAULT 0,
                PRIMARY KEY (site_id, product_id)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_watchlist_site ON watchlist (site_id)"
        )
        self._migrate_add_image_url()
        self._conn.commit()

    def _migrate_add_image_url(self) -> None:
        """ADD COLUMN image_url cho DB cũ chưa có cột này (SQLite không có IF NOT EXISTS cho ADD)."""
        for table in ("catalog", "watchlist"):
            cols = {r[1] for r in self._conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "image_url" not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN image_url TEXT NOT NULL DEFAULT ''")

    @staticmethod
    def _key(site_id: str, keyword: str) -> str:
        return f"{site_id}:{keyword.strip().lower()}"

    def get(self, site_id: str, keyword: str) -> list[DrugPrice] | None:
        """Trả về data còn hạn, hoặc None nếu miss/hết hạn."""
        cur = self._conn.execute(
            "SELECT payload, crawled_at, ttl_hours FROM crawl_cache WHERE cache_key = ?",
            (self._key(site_id, keyword),),
        )
        row = cur.fetchone()
        if row is None:
            return None

        payload, crawled_at, ttl_hours = row
        if self._is_expired(crawled_at, ttl_hours):
            return None

        try:
            items = json.loads(payload)
            return [DrugPrice(**item) for item in items]
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def set(self, site_id: str, keyword: str, data: list[DrugPrice], ttl_hours: float) -> None:
        payload = json.dumps(
            [json.loads(p.model_dump_json()) for p in data], ensure_ascii=False
        )
        self._conn.execute(
            """
            INSERT INTO crawl_cache (cache_key, site_id, keyword, payload, crawled_at, ttl_hours)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload=excluded.payload,
                crawled_at=excluded.crawled_at,
                ttl_hours=excluded.ttl_hours
            """,
            (self._key(site_id, keyword), site_id, keyword.strip().lower(), payload, time.time(), ttl_hours),
        )
        self._conn.commit()
        self.record_history(data)

    # ------------------------------------------------------------ price history
    def record_history(self, data: list[DrugPrice]) -> int:
        """Ghi snapshot giá vào price_history; chỉ ghi khi giá đổi. Trả về số dòng ghi."""
        now = time.time()
        written = 0
        for p in data:
            if p.price_vnd <= 0:
                continue  # giá ẩn/chưa đăng nhập — không phải diễn biến giá thật
            source = p.source.value if hasattr(p.source, "value") else str(p.source)
            cur = self._conn.execute(
                "SELECT price_vnd FROM price_history"
                " WHERE drug_name = ? AND source = ?"
                " ORDER BY recorded_at DESC LIMIT 1",
                (p.drug_name, source),
            )
            row = cur.fetchone()
            if row is not None and row[0] == p.price_vnd:
                continue
            self._conn.execute(
                "INSERT INTO price_history (drug_name, source, price_vnd, recorded_at)"
                " VALUES (?, ?, ?, ?)",
                (p.drug_name, source, p.price_vnd, now),
            )
            written += 1
        if written:
            self._conn.commit()
        return written

    def get_history(self, drug_name: str) -> list[dict]:
        """Diễn biến giá của một thuốc (mọi nguồn), cũ → mới."""
        cur = self._conn.execute(
            "SELECT drug_name, source, price_vnd, recorded_at FROM price_history"
            " WHERE lower(drug_name) = ? ORDER BY recorded_at ASC, id ASC",
            (drug_name.strip().lower(),),
        )
        return [
            {"drug_name": r[0], "source": r[1], "price_vnd": r[2], "recorded_at": r[3]}
            for r in cur.fetchall()
        ]

    def recent_changes(self, since_epoch: float) -> list[dict]:
        """Các lần đổi giá từ `since_epoch`: có bản ghi trước đó với giá khác."""
        cur = self._conn.execute(
            """
            SELECT drug_name, source, price_vnd, prev_price, recorded_at FROM (
                SELECT drug_name, source, price_vnd, recorded_at,
                       LAG(price_vnd) OVER (
                           PARTITION BY drug_name, source ORDER BY recorded_at, id
                       ) AS prev_price
                FROM price_history
            )
            WHERE recorded_at >= ? AND prev_price IS NOT NULL AND prev_price != price_vnd
            ORDER BY recorded_at ASC
            """,
            (since_epoch,),
        )
        return [
            {
                "drug_name": r[0],
                "source": r[1],
                "price_vnd": r[2],
                "prev_price_vnd": r[3],
                "recorded_at": r[4],
            }
            for r in cur.fetchall()
        ]

    def age_hours(self, site_id: str, keyword: str) -> float | None:
        """Tuổi cache (giờ) — dùng cho log 'cache hit (age: 2h)'."""
        cur = self._conn.execute(
            "SELECT crawled_at FROM crawl_cache WHERE cache_key = ?",
            (self._key(site_id, keyword),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return (time.time() - row[0]) / 3600.0

    def _iter_live_items(self):
        """Duyệt mọi DrugPrice trong cache còn hạn (mọi site, mọi keyword)."""
        cur = self._conn.execute(
            "SELECT payload, crawled_at, ttl_hours FROM crawl_cache"
        )
        for payload, crawled_at, ttl_hours in cur.fetchall():
            if self._is_expired(crawled_at, ttl_hours):
                continue
            try:
                for item in json.loads(payload):
                    yield item
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

    def suggest_names(self, prefix: str, limit: int = 30) -> list[str]:
        """Tên thuốc (unique) trong cache còn hạn khớp prefix — cho autocomplete."""
        q = prefix.strip().lower()
        seen: dict[str, None] = {}
        for item in self._iter_live_items():
            name = (item.get("drug_name") or "").strip()
            if not name:
                continue
            if not q or q in name.lower():
                seen.setdefault(name, None)
            if len(seen) >= limit:
                break
        return list(seen.keys())

    def all_live_names(self) -> list[str]:
        """Mọi tên thuốc (unique, giữ thứ tự) trong cache còn hạn — cho gom canonical."""
        seen: dict[str, None] = {}
        for item in self._iter_live_items():
            name = (item.get("drug_name") or "").strip()
            if name:
                seen.setdefault(name, None)
        return list(seen.keys())

    def find_by_names(self, names: list[str]) -> list[DrugPrice]:
        """Mọi bản ghi giá khớp một trong các tên (dùng khi gom biến thể canonical)."""
        targets = {n.strip().lower() for n in names if n.strip()}
        out: list[DrugPrice] = []
        for item in self._iter_live_items():
            if (item.get("drug_name") or "").strip().lower() in targets:
                try:
                    out.append(DrugPrice(**item))
                except (TypeError, ValueError):
                    continue
        return out

    def find_by_name(self, name: str) -> list[DrugPrice]:
        """Mọi bản ghi giá (mọi nguồn) khớp đúng tên thuốc, từ cache còn hạn."""
        target = name.strip().lower()
        out: list[DrugPrice] = []
        for item in self._iter_live_items():
            if (item.get("drug_name") or "").strip().lower() == target:
                try:
                    out.append(DrugPrice(**item))
                except (TypeError, ValueError):
                    continue
        return out

    @staticmethod
    def _is_expired(crawled_at: float, ttl_hours: float) -> bool:
        return (time.time() - crawled_at) > ttl_hours * 3600.0

    def clear(self) -> None:
        self._conn.execute("DELETE FROM crawl_cache;")
        self._conn.commit()

    # ----------------------------------------------------------- catalog
    def upsert_catalog_items(self, items: list[CatalogItem]) -> int:
        """Upsert catalog items (INSERT OR REPLACE). Trả về số dòng lưu."""
        if not items:
            return 0
        rows = [
            (i.source.value, i.product_id, i.drug_name, i.search_name,
             i.manufacturer, i.source_url, i.image_url, i.cached_at.timestamp())
            for i in items
        ]
        self._conn.executemany(
            """
            INSERT OR REPLACE INTO catalog
                (source, product_id, drug_name, search_name, manufacturer, source_url, image_url, cached_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        self._conn.commit()
        return len(rows)

    def catalog_suggest(self, prefix: str, limit: int = 30) -> list[CatalogItem]:
        """Catalog items where drug_name OR search_name matches prefix (case-insensitive)."""
        q = f"%{prefix.strip().lower()}%"
        cur = self._conn.execute(
            """
            SELECT source, product_id, drug_name, search_name, manufacturer, source_url, image_url, cached_at
            FROM catalog
            WHERE lower(drug_name) LIKE ? OR lower(search_name) LIKE ?
            ORDER BY drug_name
            LIMIT ?
            """,
            (q, q, limit),
        )
        return [self._row_to_catalog_item(r) for r in cur.fetchall()]

    def catalog_find(self, query: str, limit: int = 50) -> list[CatalogItem]:
        """Find catalog items by substring (drug_name or search_name)."""
        q = f"%{query.strip().lower()}%"
        cur = self._conn.execute(
            """
            SELECT source, product_id, drug_name, search_name, manufacturer, source_url, image_url, cached_at
            FROM catalog
            WHERE lower(drug_name) LIKE ? OR lower(search_name) LIKE ?
            ORDER BY drug_name
            LIMIT ?
            """,
            (q, q, limit),
        )
        return [self._row_to_catalog_item(r) for r in cur.fetchall()]

    def catalog_find_by_url(self, url: str) -> CatalogItem | None:
        """Tra 1 item catalog theo đúng source_url — dùng cho tính năng 'Thêm bằng
        URL' (GUI dán URL sản phẩm copy từ 1 trong 9 site). Bỏ dấu '/' cuối ở cả 2
        phía để không lệch do copy-paste (URL kèm/không kèm trailing slash)."""
        normalized = url.strip().rstrip("/")
        if not normalized:
            return None
        cur = self._conn.execute(
            """
            SELECT source, product_id, drug_name, search_name, manufacturer, source_url, image_url, cached_at
            FROM catalog
            WHERE rtrim(source_url, '/') = ?
            LIMIT 1
            """,
            (normalized,),
        )
        row = cur.fetchone()
        return self._row_to_catalog_item(row) if row else None

    def catalog_age_hours(self, source: str) -> float | None:
        """Age in hours of newest catalog entry for a source (None if no entries)."""
        cur = self._conn.execute(
            "SELECT MAX(cached_at) FROM catalog WHERE source = ?",
            (source,),
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return None
        return (time.time() - row[0]) / 3600.0

    def catalog_count(self, source: str | None = None) -> int:
        """Count catalog items, optionally filtered by source."""
        if source is not None:
            cur = self._conn.execute("SELECT COUNT(*) FROM catalog WHERE source = ?", (source,))
        else:
            cur = self._conn.execute("SELECT COUNT(*) FROM catalog")
        return cur.fetchone()[0]

    def catalog_clear(self, source: str | None = None) -> None:
        """Clear catalog entries, optionally per-source."""
        if source is not None:
            self._conn.execute("DELETE FROM catalog WHERE source = ?", (source,))
        else:
            self._conn.execute("DELETE FROM catalog")
        self._conn.commit()

    @staticmethod
    def _row_to_catalog_item(r: tuple[Any, ...]) -> CatalogItem:
        from datetime import datetime as _dt
        return CatalogItem(
            source=SourceName(r[0]),
            product_id=r[1],
            drug_name=r[2],
            search_name=r[3],
            manufacturer=r[4],
            source_url=r[5],
            image_url=r[6],
            cached_at=_dt.fromtimestamp(r[7]),
        )

    # --------------------------------------------------------- watchlist
    def add_to_watchlist(self, item: WatchlistItem) -> None:
        """INSERT OR REPLACE into watchlist."""
        self._conn.execute(
            """
            INSERT OR REPLACE INTO watchlist
                (site_id, source, product_id, drug_name, search_name, image_url,
                 added_at, last_price_vnd, last_checked)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item.site_id, item.source.value, item.product_id, item.drug_name,
             item.search_name, item.image_url, item.added_at, item.last_price_vnd, item.last_checked),
        )
        self._conn.commit()

    def remove_from_watchlist(self, product_id: str, site_id: str) -> bool:
        """Delete from watchlist. Returns True if a row was deleted."""
        cur = self._conn.execute(
            "DELETE FROM watchlist WHERE site_id = ? AND product_id = ?",
            (site_id, product_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_watchlist(self) -> list[WatchlistItem]:
        """All watchlist items, ordered by added_at."""
        cur = self._conn.execute(
            "SELECT site_id, source, product_id, drug_name, search_name, image_url,"
            " added_at, last_price_vnd, last_checked"
            " FROM watchlist ORDER BY added_at"
        )
        return [self._row_to_watchlist_item(r) for r in cur.fetchall()]

    def get_watchlist_by_source(self) -> dict[str, list[WatchlistItem]]:
        """Watchlist items grouped by site_id."""
        cur = self._conn.execute(
            "SELECT site_id, source, product_id, drug_name, search_name, image_url,"
            " added_at, last_price_vnd, last_checked"
            " FROM watchlist ORDER BY site_id, added_at"
        )
        out: dict[str, list[WatchlistItem]] = {}
        for r in cur.fetchall():
            item = self._row_to_watchlist_item(r)
            out.setdefault(item.site_id, []).append(item)
        return out

    def update_watchlist_price(
        self, product_id: str, site_id: str, price_vnd: int, checked_at: float
    ) -> None:
        """Update last_price_vnd and last_checked for a watchlist item."""
        self._conn.execute(
            "UPDATE watchlist SET last_price_vnd = ?, last_checked = ?"
            " WHERE site_id = ? AND product_id = ?",
            (price_vnd, checked_at, site_id, product_id),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_watchlist_item(r: tuple[Any, ...]) -> WatchlistItem:
        return WatchlistItem(
            site_id=r[0],
            source=SourceName(r[1]),
            product_id=r[2],
            drug_name=r[3],
            search_name=r[4],
            image_url=r[5],
            added_at=r[6],
            last_price_vnd=r[7],
            last_checked=r[8],
        )

    def close(self) -> None:
        self._conn.close()

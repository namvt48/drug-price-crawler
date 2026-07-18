"""Watchlist scheduler — refresh giá watchlist định kỳ (mặc định 10 phút).

Ví von: như nhân viên khảo giá được giao "sổ theo dõi" — cứ đều đặn
10 phút đi hỏi giá các món trong sổ, ghi giá mới, hô lên nếu giá đổi.

Chạy:
    python -m crawlers.watchlist_scheduler                    # loop 10 phút
    python -m crawlers.watchlist_scheduler --once             # 1 vòng rồi thoát
    python -m crawlers.watchlist_scheduler --interval 5       # 5 phút/vòng
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import datetime

from crawlers.engine import CrawlerEngine


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def run_watchlist_cycle(engine: CrawlerEngine, log=_log) -> int:
    """Một vòng: refresh giá watchlist → ghi history → phát hiện giá đổi."""
    started = time.time()
    updated = await engine.refresh_watchlist_prices()
    changes = engine.cache.recent_changes(started)
    if changes:
        for c in changes:
            pct = _change_pct(c["prev_price_vnd"], c["price_vnd"])
            log(
                f"⚠ GIÁ ĐỔI [{c['source']}] {c['drug_name']}: "
                f"{c['prev_price_vnd']:,}đ → {c['price_vnd']:,}đ ({pct:+.1f}%)"
            )
    else:
        log(f"Không có thay đổi giá (đã refresh {updated} mục).")
    return updated


def _change_pct(old: int, new: int) -> float:
    return (new - old) / old * 100.0 if old else 0.0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Drug Price Crawler — Watchlist Scheduler")
    p.add_argument("--interval", type=float, default=None, help="Chu kỳ refresh (phút). Mặc định từ config.")
    p.add_argument("--once", action="store_true", help="Chạy 1 vòng rồi thoát.")
    p.add_argument("--config", default=None, help="Đường dẫn accounts.yaml.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    engine = CrawlerEngine(config_path=args.config, log=_log, use_cache=True)
    interval_min = args.interval or engine.watchlist_config.refresh_interval_minutes
    try:
        while True:
            asyncio.run(run_watchlist_cycle(engine))
            if args.once:
                return 0
            _log(f"Ngủ {interval_min} phút đến vòng kế tiếp (Ctrl+C để dừng).")
            time.sleep(interval_min * 60.0)
    except KeyboardInterrupt:
        _log("Đã dừng watchlist scheduler.")
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())

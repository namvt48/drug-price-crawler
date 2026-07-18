"""Scheduler — crawl định kỳ và cảnh báo khi giá thay đổi.

Ví von: như nhân viên đi khảo giá mỗi sáng — đúng giờ đi một vòng 9 chợ,
về đối chiếu với sổ giá hôm trước (price_history), chỗ nào giá đổi thì
ghi vào sổ cảnh báo (price_alerts.csv) và hô lên cho chủ nhà thuốc biết.

Chạy:
    python -m crawlers.scheduler -k boganic --interval-hours 24
    python -m crawlers.scheduler -k boganic --once        # 1 vòng rồi thoát
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from datetime import datetime
from pathlib import Path

from crawlers.engine import CrawlerEngine
from utils.excel_writer import writer_for

ALERT_HEADERS = [
    "detected_at", "drug_name", "source",
    "old_price_vnd", "new_price_vnd", "change_pct",
]


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


async def run_cycle(
    engine: CrawlerEngine,
    keyword: str,
    out_path: str | Path,
    alerts_path: str | Path,
    site_ids: list[str] | None = None,
    log=_log,
) -> list[dict]:
    """Một vòng: crawl mới (bỏ cache đọc) → export → phát hiện giá đổi. Trả về changes."""
    started = time.time()
    prices = await engine.crawl(keyword, site_ids=site_ids, force_refresh=True)
    if prices:
        total = writer_for(out_path).write(prices)
        log(f"Đã ghi {len(prices)} bản ghi → {out_path} (tổng {total} dòng).")
    else:
        log("Vòng crawl không có kết quả.")

    changes = engine.cache.recent_changes(started)
    if changes:
        _append_alerts(alerts_path, changes)
        for c in changes:
            pct = _change_pct(c["prev_price_vnd"], c["price_vnd"])
            log(
                f"⚠ GIÁ ĐỔI [{c['source']}] {c['drug_name']}: "
                f"{c['prev_price_vnd']:,}đ → {c['price_vnd']:,}đ ({pct:+.1f}%)"
            )
        log(f"Đã ghi {len(changes)} cảnh báo → {alerts_path}")
    else:
        log("Không có thay đổi giá so với lần crawl trước.")
    return changes


def _change_pct(old: int, new: int) -> float:
    return (new - old) / old * 100.0 if old else 0.0


def _append_alerts(path: str | Path, changes: list[dict]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    is_new = not p.exists()
    with p.open("a", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ALERT_HEADERS)
        if is_new:
            writer.writeheader()
        for c in changes:
            writer.writerow({
                "detected_at": datetime.fromtimestamp(c["recorded_at"]).isoformat(timespec="seconds"),
                "drug_name": c["drug_name"],
                "source": c["source"],
                "old_price_vnd": c["prev_price_vnd"],
                "new_price_vnd": c["price_vnd"],
                "change_pct": f"{_change_pct(c['prev_price_vnd'], c['price_vnd']):.1f}",
            })


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Drug Price Crawler — Scheduler (crawl định kỳ)")
    p.add_argument("-k", "--keyword", default="", help="Từ khóa crawl (vd: boganic).")
    p.add_argument("-s", "--sites", default="", help="Danh sách site_id, phân cách dấu phẩy. Rỗng = tất cả.")
    p.add_argument("-o", "--out", default="output/prices.csv", help="File export (.csv hoặc .xlsx).")
    p.add_argument("--alerts", default="output/price_alerts.csv", help="File CSV cảnh báo giá đổi.")
    p.add_argument("--interval-hours", type=float, default=24.0, help="Chu kỳ crawl (giờ, mặc định 24).")
    p.add_argument("--once", action="store_true", help="Chạy đúng 1 vòng rồi thoát (test/cron ngoài).")
    p.add_argument("--config", default=None, help="Đường dẫn accounts.yaml.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    site_ids = [s.strip() for s in args.sites.split(",") if s.strip()] or None

    engine = CrawlerEngine(config_path=args.config, log=_log, use_cache=True)
    try:
        while True:
            asyncio.run(
                run_cycle(engine, args.keyword.strip(), args.out, args.alerts, site_ids)
            )
            if args.once:
                return 0
            _log(f"Ngủ {args.interval_hours}h đến vòng kế tiếp (Ctrl+C để dừng).")
            time.sleep(args.interval_hours * 3600.0)
    except KeyboardInterrupt:
        _log("Đã dừng scheduler.")
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI headless — chạy crawl không cần GUI.

Ví dụ:
    python cli.py --keyword boganic --sites giathuoctot,chothuoc247 --out output/prices.csv
    python cli.py -k paracetamol            # tất cả site enabled
    python cli.py -k boganic --no-cache
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import yaml

from crawlers.engine import CrawlerEngine
from utils.excel_writer import writer_for
from utils.trial_manager import TrialManager


def _log(msg: str) -> None:
    print(msg, flush=True)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Drug Price Crawler — CLI")
    p.add_argument("-k", "--keyword", default="", help="Từ khóa tìm kiếm (vd: boganic)")
    p.add_argument("-s", "--sites", default="", help="Danh sách site_id, phân cách dấu phẩy. Rỗng = tất cả.")
    p.add_argument("-o", "--out", default="output/prices.csv", help="File xuất: .csv hoặc .xlsx (Excel có tô màu giá rẻ nhất).")
    p.add_argument("--config", default=None, help="Đường dẫn accounts.yaml (mặc định config/accounts.yaml).")
    p.add_argument("--no-cache", action="store_true", help="Bỏ qua cache, luôn crawl mới.")
    p.add_argument("--list-sites", action="store_true", help="Liệt kê site khả dụng rồi thoát.")
    p.add_argument("--history", default=None, metavar="NAME", help="In diễn biến giá của một thuốc (từ price_history) rồi thoát.")
    p.add_argument("--crawl-catalog", action="store_true", help="Crawl catalog (id+tên, không giá) cho các site được chọn rồi thoát.")
    p.add_argument("--watchlist", action="store_true", help="In watchlist hiện tại rồi thoát.")
    p.add_argument("--add-watchlist", default=None, metavar="QUERY", help="Tìm catalog theo query, thêm kết quả đầu tiên vào watchlist rồi thoát.")
    p.add_argument("--refresh-watchlist", action="store_true", help="Refresh giá watchlist 1 lần rồi thoát (không loop).")
    p.add_argument("--manufacturer", action="append", default=None, metavar="NAME", help="Chỉ giữ bản ghi của hãng này (lặp lại được; ghi đè block filters trong config).")
    p.add_argument("--contains", action="append", default=None, metavar="KEYWORD", help="Chỉ giữ thuốc có tên chứa từ khóa này (lặp lại được).")
    p.add_argument(
        "--dump-groups",
        default=None,
        metavar="PATH",
        help="Ghi file YAML các nhóm canonical để review trước khi copy vào name_aliases.yaml.",
    )
    return p.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    engine = CrawlerEngine(
        config_path=args.config, log=_log, use_cache=not args.no_cache
    )
    try:
        if args.list_sites:
            for s in engine.available_sites():
                print(f"  {s.id:16s} {s.name}")
            return 0

        if args.history:
            return _print_history(engine, args.history)

        if args.crawl_catalog:
            site_ids = [s.strip() for s in args.sites.split(",") if s.strip()] or None
            count = await engine.crawl_catalog(site_ids=site_ids, force_refresh=args.no_cache)
            _log(f"Catalog: {count} mục đã lưu.")
            return 0

        if args.watchlist:
            return _print_watchlist(engine)

        if args.add_watchlist:
            return _add_watchlist(engine, args.add_watchlist)

        if args.refresh_watchlist:
            count = await engine.refresh_watchlist_prices()
            _log(f"Đã refresh {count} mục watchlist.")
            return 0

        # Filter override từ CLI (thay block filters: trong config nếu có flag).
        if args.manufacturer:
            engine.filters.manufacturers = args.manufacturer
        if args.contains:
            engine.filters.name_keywords = args.contains

        keyword = args.keyword.strip()
        if not keyword:
            _log("Không có từ khóa → crawl tất cả sản phẩm (trang 1).")

        site_ids = [s.strip() for s in args.sites.split(",") if s.strip()] or None
        prices = await engine.crawl(keyword, site_ids=site_ids, progress=_progress)

        if not prices:
            _log("Không có kết quả nào.")
            return 1

        if args.dump_groups:
            from utils.normalizer import group_names

            names = [p.drug_name for p in prices]
            groups = group_names(names)
            dump_path = Path(args.dump_groups)
            dump_path.parent.mkdir(parents=True, exist_ok=True)
            with dump_path.open("w", encoding="utf-8") as fh:
                yaml.safe_dump(groups, fh, allow_unicode=True, sort_keys=False)
            _log(f"Đã ghi {len(groups)} nhóm → {args.dump_groups}")

        total = writer_for(args.out).write(prices)
        _log(f"Đã ghi {len(prices)} bản ghi mới → {args.out} (tổng {total} dòng).")
        return 0
    finally:
        engine.close()


def _print_history(engine: CrawlerEngine, name: str) -> int:
    """In diễn biến giá 'thời điểm | nguồn | giá' của một thuốc, cũ → mới."""
    from datetime import datetime

    rows = engine.get_history(name)
    if not rows:
        _log(f"Chưa có lịch sử giá cho '{name}' (crawl vài lần để tích lũy).")
        return 1
    _log(f"Lịch sử giá '{name}' ({len(rows)} mốc):")
    for r in rows:
        ts = datetime.fromtimestamp(r["recorded_at"]).strftime("%Y-%m-%d %H:%M")
        _log(f"  {ts}  {r['source']:16s} {r['price_vnd']:>12,}đ")
    return 0


def _print_watchlist(engine: CrawlerEngine) -> int:
    """In watchlist hiện tại: tên, nguồn, giá cuối, lần kiểm tra cuối."""
    from datetime import datetime

    items = engine.get_watchlist()
    if not items:
        _log("Watchlist trống.")
        return 0
    _log(f"Watchlist ({len(items)} mục):")
    for item in items:
        ts = (
            datetime.fromtimestamp(item.last_checked).strftime("%Y-%m-%d %H:%M")
            if item.last_checked
            else "—"
        )
        price_str = f"{item.last_price_vnd:,}đ" if item.last_price_vnd else "—"
        _log(
            f"  {item.drug_name:40s} [{item.site_id:16s}] {price_str:>12s}  (check: {ts})"
        )
    return 0


def _add_watchlist(engine: CrawlerEngine, query: str) -> int:
    """Tìm catalog theo query, thêm kết quả đầu tiên vào watchlist."""
    results = engine.suggest_catalog(query, limit=10)
    if not results:
        _log(f"Không tìm thấy trong catalog: '{query}'")
        _log("Chạy 'python cli.py --crawl-catalog' trước.")
        return 1
    engine.add_to_watchlist(results[0])
    _log(f"Đã thêm: {results[0].drug_name} [{results[0].source.value}]")
    return 0


def _progress(done: int, total: int) -> None:
    _log(f"  ...tiến độ: {done}/{total} nguồn")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if not args.list_sites:
        trial = TrialManager().check()
        _log(trial.message)
        if not trial.is_valid:
            return 2

    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())

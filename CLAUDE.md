# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A crawler that collects **wholesale (B2B) drug prices** from 9 Vietnamese pharmacy websites, normalizes them into a single `DrugPrice` model, and caches the results. All 9 sites require **login to see prices** — guest/unauthenticated requests return `basePrice = 0` (prices hidden). Authenticated crawling is the entire point.

## Current state

Implemented per `PLAN.md`. Full Python source exists: `main.py`, `cli.py`, `crawlers/` (base, engine, cache_manager, scheduler, `b2b/` × 9), `gui/` (main_window = display only, viewmodel = pure logic), `utils/` (models, config_loader, csv_writer, excel_writer, filters, normalizer, price_parser, trial_manager). `PLAN.md` = product spec; `docs/*.md` = confirmed per-site API specs (source of truth for each crawler's fetch logic). Domain comments are in Vietnamese — keep that.

Live-tested 2026-07-11: all 9 sites work end-to-end (login + real price). `thuocsi` and `thuoctot3mien` needed their crawler code fixed this session (docs were stale — see `docs/thuocsi.md` §2.2/§3.2b and `docs/thuoctot3mien.md` §2.1 for what was actually wrong and how it was found). `bachhoathuoc` needed a category-partitioned catalog crawl (server ignores `keyword`, only filters by `slug`, and hard-caps `page*pageSize<=5000`) plus a live per-SKU price lookup — see `docs/bachhoathuoc.md` §3.2 and `crawlers/b2b/bachhoathuoc.py`. Status table: `README.md`.

## Commands

```bash
pip install -r requirements.txt              # deps (selectolax needs a wheel/build)
cp config/accounts.example.yaml config/accounts.yaml   # then fill real accounts
python cli.py --list-sites                   # list the 9 sources
python cli.py -k boganic -s giathuoctot      # crawl one site (real login + fetch)
python cli.py -k boganic --no-cache          # all enabled sites, skip cache
python cli.py -k boganic -o out.xlsx         # .xlsx extension → Excel export (cheapest highlighted)
python cli.py --history "<drug name>"        # price history from output/cache.db
python -m crawlers.scheduler -k boganic --once   # one scheduled cycle + price-change alerts
python main.py                               # no args → tkinter GUI
build.bat                                     # Windows only → dist/DrugPriceCrawler.exe
```

Tests: `pip install -r requirements-dev.txt && pytest` — coverage gate ≥90% (`pytest.ini`), `gui/main_window.py` excluded (`.coveragerc`; GUI logic lives in testable `gui/viewmodel.py`). CI runs the same on push/PR (`.github/workflows/ci.yml`). For crawler changes also run a live single-site crawl of a working source (giathuoctot).

## Security (CRITICAL)

`config/accounts.yaml` contains **plaintext usernames and passwords** and is gitignored (do not commit). `config/accounts.example.yaml` holds placeholders. Not yet a git repo — run `git init` before any commit and confirm `.gitignore` covers `config/accounts.yaml`, `output/`, `*.csv`, `*.db`. The `.exe` reads `config/accounts.yaml` from **next to the executable** (via `config_loader.app_base_dir()`), never bundled into the binary. For hard-login sites (chothuoctot, bachhoathuoc) paste an `access_token` into `sites.<id>.auth.manual_token`.

## Architecture

- **Stack**: Python async — `httpx.AsyncClient` + `asyncio`, `selectolax` (HTML parse), `pydantic` (models), `pyyaml` (config), stdlib `sqlite3`/`csv`/`tkinter`. No Playwright at runtime (analysis-only).
- **`crawlers/base.py` `BaseCrawler`**: shared engine — httpx client, retry+backoff, rate-limit throttle, and the "AuthManager" logic (`ensure_auth`, re-auth on 401/redirect). Each site subclass implements only 3 abstracts: `_login()`, `_fetch_products(keyword)`, `_parse_product(raw)`. Auth is per-crawler (not a central manager) because the 9 flows differ wildly (JWT / session+CSRF / OAuth PKCE / WordPress nonce). `manual_token` in config short-circuits `_login()`.
- **`crawlers/engine.py` `CrawlerEngine`**: loads config, runs selected sites concurrently (`asyncio.gather`), applies cache, merges results; one site failing never kills the batch. `crawlers/b2b/__init__.py` `CRAWLER_REGISTRY` maps `site_id → class`.
- **Model** `DrugPrice` (`utils/models.py`): `price_vnd` is `int` (VND has no decimals — never `float`). A `field_validator` coerces `None → ""` for string fields (APIs return null often). `source` is `SourceName` enum, one per site.
- **Config-driven**: `config_loader.load_sites()` deep-merges each site over the `defaults:` block. Never hardcode credentials, URLs, page sizes, or delays — read from `SiteConfig`.
- **CSV** (`utils/csv_writer.py`): dedup by `(drug_name + source)`, written `utf-8-sig` so Excel renders Vietnamese.

### Cross-cutting concerns (patterns repeat across all site docs)
- **Auth + re-auth**: cache the token/session; on `401` re-login and retry (max 3). JWT sites (giathuoctot) refresh before expiry, then fall back to full re-login. See per-doc "Auth Strategy for Crawler".
- **Cache**: key per site (e.g. `giathuoctot:all_products`), TTL from config (default 24h). Check cache → on miss/expired, crawl → store.
- **Rate limiting**: `delay_seconds` between requests (default 2s, some sites 3s), retries with exponential backoff (5s → 10s → 20s). Send a real desktop Chrome `User-Agent`; several sites sit behind Cloudflare.

## Per-site variance (the important gotchas)

Each site is a different platform with a different auth mechanism — do **not** assume one crawler shape fits all. Read the matching `docs/<site>.md` before implementing that site.

| Site | Platform | Auth | Data access | Notes |
|---|---|---|---|---|
| giathuoctot | Angular SPA | JWT Bearer (`/authentication/account/v2/login`) | JSON API `api.giathuoctot.com`, POST `retrieve-products-client` | **Use `limit`+`offset`, not `page`** (page params are ignored → stuck at 30). 200/req max. `-client`/`-member` return price, `-guest` returns 0. |
| chothuoc247 | Laravel + jQuery | Session cookie + CSRF `_token` (from `<meta csrf-token>`) | 18 products/page, ~174 pages | Must scrape CSRF token from login HTML first. |
| thuochapu | Joomla | Session cookie + Joomla security token (hidden field) | Server-rendered HTML, 60/page, ~14 pages | No AJAX for listing; price also in JSON-LD `schema.org/Offer` on detail pages. Short session (15–60 min inactivity). |
| thuocsi | Next.js + Buymed | Basic Auth (`PARTNER/v2.frontend.web`) **+** Bearer after login | JSON via `thuocsi.vn/backend/` gateway | Three auth modes (`isBasic`/`isAuth`/none) per endpoint. |
| bachhoathuoc | Next.js + Teko | **OAuth 2.0 (Auth Code + PKCE)** | Teko APIs (`discovery`/`search.tekoapis.com`) | Most complex flow; client_id/terminal/platform IDs in doc. |
| thuocsisaigon | Haravan + ASP.NET | Session cookie + ASP.NET Antiforgery token | HTML only, no JSON API | `__RequestVerificationToken` from HTML; form-urlencoded login. |
| duocphamgiasi | WordPress + WooCommerce | WP session cookie + nonce (Meta Box login) | HTML only; WooCommerce REST API disabled (404) | Scrape `mbup_key`/`nonce` from `/tai-khoan`. |
| chothuoctot | Next.js App Router + Medlink | Token-based (`access_token`, Redux) | JSON `api.medlink.vn/pharmacy/` | Ant Design login form; behind Cloudflare. |
| thuoctot3mien | Next.js App Router + Laravel | Token-based (`accessToken`, localStorage) | JSON `api.thuoctot3mien.vn/api/web/v1` | Login field is named `email` but takes a **phone number**; `phone`/`username` → 422. |

Broadly: **giathuoctot, thuocsi, bachhoathuoc, chothuoctot, thuoctot3mien** expose JSON APIs (preferred). **chothuoc247, thuochapu, thuocsisaigon, duocphamgiasi** are HTML-scrape targets.

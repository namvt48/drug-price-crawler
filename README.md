# PharmaPrice

Tool crawl **giá thuốc sỉ (B2B)** từ 9 website dược Việt Nam cần đăng nhập →
xuất CSV/Excel. Có GUI (tkinter, gom biến thể + highlight giá rẻ nhất), CLI,
scheduler crawl định kỳ + cảnh báo giá đổi, lịch sử giá, whitelist filter,
cache theo TTL, tự đăng nhập lại khi hết session.
Đóng gói được thành 1 file `.exe` portable cho Windows.

## Cài đặt

```bash
pip install -r requirements.txt

# Tạo config từ mẫu rồi điền tài khoản thật cho từng site:
cp config/accounts.example.yaml config/accounts.yaml
```

> ⚠️ `config/accounts.yaml` chứa **mật khẩu** — đã nằm trong `.gitignore`, KHÔNG commit.

## Dùng CLI

```bash
python cli.py --list-sites                          # xem 9 nguồn
python cli.py -k boganic                            # crawl tất cả nguồn enabled
python cli.py -k boganic -s giathuoctot,chothuoc247 # chỉ vài nguồn
python cli.py -k paracetamol --no-cache -o output/gia.csv
python cli.py -k boganic -o output/gia.xlsx         # đuôi .xlsx → Excel (tô xanh giá rẻ nhất)
python cli.py -k boganic --manufacturer Traphaco    # whitelist theo hãng (lặp lại được)
python cli.py -k thuoc --contains boganic           # whitelist theo từ khóa tên
python cli.py --history "Boganic siro lọ 100ml Traphaco"  # diễn biến giá theo thời gian
```

Whitelist còn cấu hình được cố định qua block `filters:` trong `config/accounts.yaml`
(xem `accounts.example.yaml`) — flag CLI ghi đè config.

## Scheduler (crawl định kỳ + cảnh báo giá đổi)

```bash
python -m crawlers.scheduler -k boganic --interval-hours 24   # mỗi sáng 1 vòng
python -m crawlers.scheduler -k boganic --once                # 1 vòng rồi thoát (hợp cron/Task Scheduler)
```

Mỗi vòng: crawl mới (bỏ cache đọc) → export CSV/Excel → so với lịch sử giá
(`price_history` trong `output/cache.db`) → giá đổi thì log `⚠ GIÁ ĐỔI` và
append vào `output/price_alerts.csv` (giá cũ / mới / % thay đổi).

## Dùng GUI

```bash
python main.py        # không tham số → mở GUI
```

Chọn nguồn (checkbox) → nhập từ khóa → chọn file CSV → **Bắt đầu**. Log + progress
hiện real-time; crawl chạy ở thread riêng nên không treo cửa sổ.

Bản dùng thử có hiệu lực **14 ngày** kể từ lần mở đầu tiên trên máy.

- Gợi ý thuốc gom theo **tên canonical** — các biến thể cùng thuốc từ nhiều nguồn
  gộp về một dòng (dùng `utils/normalizer.py` + `config/name_aliases.yaml`).
- Cột **Rẻ nhất ★** hiện nguồn giá thấp nhất; trong chuỗi giá, nguồn rẻ nhất đánh dấu ★.
- Xuất **CSV** hoặc **Excel** (.xlsx có màu).

## Đóng gói .exe (Windows)

```bat
build.bat
```

Kết quả gồm `dist\PharmaPrice.exe`, config mẫu và catalog sản phẩm chuẩn:

```text
dist/
├── PharmaPrice.exe
├── config/
│   ├── accounts.yaml
│   └── name_aliases.yaml
└── output/
    └── catalog_master.xlsx
```

`accounts.yaml` và catalog đặt **cạnh** file `.exe`, không nhúng vào binary để
giữ mật khẩu bên ngoài executable và cho phép app cập nhật catalog. GitHub Actions
upload nguyên cấu trúc này trong artifact `PharmaPrice-windows`.

## Kiến trúc

```
main.py            Entry point (GUI nếu không tham số, CLI nếu có)
cli.py             CLI headless (crawl, --history, filter flags, xuất csv/xlsx)
gui/main_window.py GUI tkinter (thread + queue bridge sang asyncio) — chỉ hiển thị
gui/viewmodel.py   Logic GUI thuần (gom canonical, giá rẻ nhất, format) — test được
crawlers/
  base.py          BaseCrawler: httpx async, retry+backoff, rate limit, tự re-auth
  engine.py        CrawlerEngine: chạy song song nhiều site + cache + filter + gộp kết quả
  cache_manager.py Cache SQLite (WAL), TTL per-site, key = site:keyword + price_history
  scheduler.py     Crawl định kỳ, cảnh báo giá đổi → output/price_alerts.csv
  b2b/*.py         9 crawler (mỗi site 1 class, tự implement _login/_fetch/_parse)
utils/
  models.py        DrugPrice, SourceName, SiteConfig, FilterConfig (pydantic)
  config_loader.py Đọc accounts.yaml, merge defaults + load_filters
  filters.py       Whitelist filter (tên/hãng/khoảng giá, không dấu)
  csv_writer.py    Ghi/append CSV, dedup theo drug_name+source, utf-8-sig (Excel VN)
  excel_writer.py  Ghi .xlsx cùng dedup, tô xanh dòng giá rẻ nhất mỗi nhóm thuốc
  normalizer.py    Gom biến thể tên thuốc liên nguồn về canonical_name
  price_parser.py  "48.000đ" -> 48000
```

## Test & CI

```bash
pip install -r requirements-dev.txt
pytest                # 297 tests, coverage gate ≥90% (pytest.ini)
```

CI GitHub Actions (`.github/workflows/ci.yml`) chạy compile-check + pytest+coverage
trên mỗi push/PR vào `main`.

Mỗi crawler kế thừa `BaseCrawler` và tự lo phần khác nhau (login + đọc sản phẩm);
phần chung (HTTP, retry, rate limit, re-auth khi 401/redirect) nằm ở base.

## Tình trạng từng nguồn (kiểm thử live 2026-07-11)

| # | Site | Auth | Trạng thái |
|---|------|------|-----------|
| 1 | giathuoctot | JWT (JSON API) | ✅ Chạy tốt — login + giá sỉ chính xác |
| 2 | chothuoc247 | Session + CSRF (JSON) | ✅ Chạy tốt |
| 3 | thuochapu | Joomla session (HTML) | ✅ Chạy tốt |
| 4 | thuocsi | Basic + Bearer | ✅ Chạy tốt — login/product-list/giá đã reverse-engineer lại đúng thực tế (docs cũ sai endpoint+field); giá bị AES-CBC mã hoá phía client, đã giải mã (`crawlers/b2b/thuocsi.py::_decrypt_price`) |
| 5 | thuoctot3mien | Bearer (JSON) | ✅ Chạy tốt — backend đòi `Origin`/`Referer` hợp lệ mới cho login, thiếu thì báo nhầm "sai mật khẩu" (docs cũ thiếu chi tiết này) |
| 6 | thuocsisaigon | Haravan + Antiforgery (HTML) | ✅ Chạy tốt |
| 7 | duocphamgiasi | WordPress nonce (HTML) | ✅ Login OK (0 kết quả cho từ khóa "boganic" cụ thể — không phải lỗi login) |
| 8 | chothuoctot | Bearer (Medlink) | ✅ Login OK (0 kết quả cho "boganic" cụ thể — không phải lỗi login) |
| 9 | bachhoathuoc | OAuth 2.0 PKCE | ✅ Chạy tốt — server bỏ qua `keyword` (chỉ lọc theo `slug` category), catalog chia theo 9 category để vượt cap `page*pageSize<=5000`; giá live theo 1 SKU qua endpoint riêng (`fetch_price_by_id`) |

Cả 9/9 nguồn đăng nhập và lấy giá thành công. Site #7/#8 trả 0 kết quả cho từ khóa
"boganic" cụ thể lúc test — không phải sự cố (login/search vẫn hoạt động), có thể
site đó không bán đúng tên sản phẩm đó lúc kiểm thử.

Chi tiết API từng site: xem `docs/<site>.md`.

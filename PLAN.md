# Drug Price Crawler — Plan & Analysis

> Tool crawl giá thuốc sỉ từ 9 nguồn B2B Việt Nam (cần đăng nhập) → xuất CSV, đóng gói portable Windows .exe

---

## 1. Mục tiêu & Phạm vi

### Mục tiêu
- Crawl giá thuốc sỉ từ **9 nguồn B2B cần đăng nhập** (giá sỉ/wholesale)
- **Cơ chế auth tự động**: đăng nhập bằng credentials trong `config/accounts.yaml`, tự re-auth khi session hết hạn
- **Cơ chế cache**: cache kết quả crawl theo TTL, chỉ re-crawl khi cache hết hạn → giảm request, tránh block
- Tự động cập nhật vào file CSV có sẵn (không duplicate, update theo key)
- Giao diện GUI đơn giản (tkinter): chọn nguồn, nhập keyword, bấm Start, xem progress
- Đóng gói thành **1 file .exe portable** — double-click là chạy, không cần cài Python

### Phạm vi
| Trong phạm vi | Ngoài phạm vi |
|---|---|
| Crawl theo từ khóa (vd: "paracetamol", "boganic") | Không crawl toàn catalog (hàng chục nghìn SKU) |
| Giá sỉ (B2B) VND | Không theo dõi giá theo thời gian thực (real-time) |
| Xuất CSV | Không xuất Excel/DB (có thể mở rộng) |
| Windows portable .exe | Không hỗ trợ macOS/Linux binary (chạy source code được) |
| Single-user, local | Không có server/cloud backend |
| Auth bằng form login / API login | Không dùng OAuth/CAPTCHA solver |

### Kết quả mong đợi (Stopping Condition)
- [ ] File .exe chạy được trên Windows 10/11 bằng double-click
- [ ] GUI hiện cửa sổ, chọn nguồn (9 site), nhập keyword, bấm Start
- [ ] Crawl trả về danh sách thuốc + giá sỉ từ 9 nguồn B2B
- [ ] Đăng nhập tự động vào 9 trang B2B bằng config, re-auth khi hết hạn
- [ ] Cache kết quả crawl theo TTL, không re-crawl khi cache còn hạn
- [ ] Xuất ra CSV đúng schema, mở bằng Excel được
- [ ] Xử lý lỗi mạng/timeout/auth-expiry mà không crash

---

## 2. Phân tích nguồn dữ liệu

### 2.1. Tổng quan 9 nguồn B2B

> **Config file**: `config/accounts.yaml` — chứa credentials + search URL + cache/auth settings cho từng site.

| # | Site | Domain | Tài khoản | Search URL Pattern | Auth Method |
|---|---|---|---|---|---|
| 1 | Giá Thuốc Tốt | giathuoctot.com | 0388279175 | `/quick-order?page=1&searchTerm={keyword}` | form_login |
| 2 | Chợ Thuốc 247 | chothuoc247.vn | 0388279175 | `/tim-kiem.html?search={keyword}&post_type=product` | form_login |
| 3 | Thuốc Hà Phú | thuochapu.com | 0388279175 | `/search.html?q={keyword}` | form_login |
| 4 | Chợ Thuốc Tốt | chothuoctot.vn | 0388279175 | `/san-pham?product_name={keyword}&...` | form_login |
| 5 | Thuốc Sĩ | thuocsi.vn | 0989872266 | `/dashboard` (search qua API nội bộ) | api_login |
| 6 | Thuốc Tốt 3 Miền | thuoctot3mien.vn | 0388279175 | `/san-pham?search={keyword}` | form_login |
| 7 | Thuốc Sĩ Sài Gòn | thuocsisaigon.vn | tvket2012@gmail.com | `/search?type=product&q=filter=...{keyword}` | form_login |
| 8 | Dược Phẩm Gia Sỉ | duocphamgiasi.vn | 0388279175 | `/?post_type=product&s={keyword}` | form_login (WordPress) |
| 9 | Bách Hóa Thuốc | sales.bachhoathuoc.com | 0388279175 | `/search?router=productListing&query={keyword}` | api_login |

### 2.2. Chiến thuật crawl theo loại auth

**Đặc điểm chung**:
- Tất cả 9 trang **yêu cầu đăng nhập** mới xem giá
- Dữ liệu là **giá sỉ** (wholesale)
- Nhiều trang dùng **PHP session / WordPress auth** (form login)
- Một số trang (thuocsi, bachhoathuoc) dùng **API token** (api_login)

#### A. Form Login (6 trang: giathuoctot, chothuoc247, thuochapu, chothuoctot, thuoctot3mien, thuocsisaigon)

```
1. POST login_url với form data: {username, password}
2. Lưu session cookie (PHPSESSID / session_id) từ response
3. GET search_url với session cookie
4. Parse HTML response → trích tên thuốc + giá
5. Nếu response redirect về login → session hết hạn → re-auth (quay lại bước 1)
```

**Flow chi tiết**:
```
[AuthManager]
   │
   ├── login(site) → POST credentials → save cookies to session jar
   │
   ├── is_authenticated(site) → check if session cookie exists & not expired
   │
   ├── ensure_auth(site) → if not authenticated or expired → login()
   │
   └── refresh_if_needed(site) → if 401/redirect-to-login detected → login()
```

#### B. API Login (2 trang: thuocsi, bachhoathuoc)

```
1. POST login API endpoint → nhận JWT/token
2. Lưu token trong header Authorization: Bearer <token>
3. GET search API với header Authorization
4. Parse JSON response → trích tên thuốc + giá
5. Nếu 401 Unauthorized → token hết hạn → re-auth
```

#### C. WordPress Login (duocphamgiasi.vn)

```
1. POST wp-login.php với form data: {log, pwd, wp-submit, redirect_to}
2. Lưu WordPress cookies (wordpress_logged_in_*, wp-postpass)
3. GET search URL với cookies
4. Parse HTML (WordPress product listing)
```

### 2.3. Cơ chế Cache

```python
# Cache flow cho mỗi site + keyword
cache_key = f"{site_id}:{keyword}"

# 1. Check cache trước khi crawl
if cache.exists(cache_key) and not cache.is_expired(cache_key):
    return cache.get(cache_key)   # Trả về data cũ, không crawl lại

# 2. Cache hết hạn hoặc chưa có → crawl mới
data = crawler.crawl(keyword)

# 3. Lưu vào cache với TTL
cache.set(cache_key, data, ttl=site.cache.ttl_hours)

# Cache storage: SQLite
# Cache key: site_id + keyword
# Cache value: list[DrugPrice] + crawled_at timestamp
```

**Cache config (trong accounts.yaml)**:
| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `cache.enabled` | `true` | Bật/tắt cache cho site này |
| `cache.ttl_hours` | `24` | Cache sống 24h, sau đó re-crawl |

### 2.4. Cơ chế Auth Refresh

```python
# Auth refresh flow
class AuthManager:
    sessions: dict[str, Session]  # site_id → session

    def ensure_auth(self, site: SiteConfig) -> Session:
        session = self.sessions.get(site.id)

        # Chưa có session → login mới
        if session is None:
            return self.login(site)

        # Session hết hạn (theo expiry_hours) → login lại
        if session.is_expired():
            return self.login(site)

        return session

    def handle_auth_failure(self, site: SiteConfig, response: Response):
        """Gọi khi response là 401 hoặc redirect về login page."""
        if site.auth.retry_on_401:
            for attempt in range(site.auth.max_auth_retries):
                session = self.login(site)
                # Retry request với session mới
                ...
            raise AuthError(f"Failed to re-authenticate after {attempts} attempts")
```

**Auth config (trong accounts.yaml)**:
| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `auth.method` | `form_login` | Cách đăng nhập (form_login / api_login) |
| `auth.session_key` | `session_id` | Tên cookie/token xác định session |
| `auth.expiry_hours` | `12` | Session hết hạn sau 12h → trigger re-auth |
| `auth.retry_on_401` | `true` | Tự re-auth khi gặp 401 |
| `auth.max_auth_retries` | `3` | Số lần thử lại đăng nhập tối đa |

### 2.5. Ưu tiên triển khai

```
Phase 1: Auth Infrastructure (AuthManager + CacheManager + BaseCrawler)
Phase 2: B2B Crawlers (9 sites — 6 form_login + 2 api_login + 1 WordPress)
Phase 3: GUI + End-to-end test
Phase 4: Packaging (.exe portable)
```

### 2.6. Lưu ý quan trọng

> ⚠️ **Bảo mật**: File `config/accounts.yaml` chứa mật khẩu. **PHẢI** thêm vào `.gitignore`, không commit lên git công khai.

> ⚠️ **Login URL cần verify**: Các `login_url` trong config là dự đoán dựa trên pattern phổ biến. Cần inspect DevTools từng trang để xác nhận:
> - Form field name chính xác (vd: `username` vs `user` vs `email` vs `log`)
> - Có CSRF token không
> - Có CAPTCHA không (nếu có → cần xử lý đặc biệt)

> ⚠️ **Thuocsi.vn**: Search URL là `/dashboard` — cần inspect DevTools để tìm search endpoint API thực tế (có thể là XHR call từ dashboard).

---

## 3. Kiến trúc Tool

### 3.1. Sơ đồ tổng thể

```
┌─────────────────────────────────────────────────────────┐
│                      GUI (tkinter)                        │
│  ┌──────────────┐  ┌─────────────────────────────────┐   │
│  │ Source        │  │  Keyword input                   │   │
│  │ checkboxes    │  │  [boganic                   ]   │   │
│  │ ☑ Giathuoctot │  │                                 │   │
│  │ ☑ ChoThuoc247 │  │  [ Start Crawl ]                │   │
│  │ ☑ ThuocSi     │  │                                 │   │
│  │ ☑ ThuocHaPu   │  │  Progress: ████████░░░ 80%      │   │
│  │ ☑ ChoThuocTot │  │                                 │   │
│  │ ☐ Thuoc3Mien  │  │  Cache: [✓] Use cache (24h TTL) │   │
│  └──────────────┘  └─────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Log:                                                 │ │
│  │ [10:30] [ThuocSi] Authenticating...                 │ │
│  │ [10:30] [ThuocSi] Login OK, searching "boganic"...  │ │
│  │ [10:31] [ThuocSi] Found 12 products (cache: miss)   │ │
│  │ [10:31] [Giathuoctot] Cache hit (age: 2h) → 8 items │ │
│  │ [10:31] [ChoThuoc247] Session expired, re-auth...   │ │
│  │ [10:32] [ChoThuoc247] Re-auth OK, found 5 products  │ │
│  │ [10:32] Writing to prices.csv...                    │ │
│  │ [10:32] Done! 25 records saved.                     │ │
│  └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   Crawler Engine                          │
│                                                           │
│  ┌──────────────────────────────────────────────────┐    │
│  │           AuthManager (session management)        │    │
│  │  • login(site) → POST credentials                │    │
│  │  • ensure_auth(site) → check session validity    │    │
│  │  • re-auth on 401/redirect                        │    │
│  │  • sessions stored in-memory (cookie jar)         │    │
│  └──────────────────────────────────────────────────┘    │
│                                                           │
│  ┌──────────────────────────────────────────────────┐    │
│  │           CacheManager (SQLite-based)             │    │
│  │  • get(site, keyword) → cached data or None       │    │
│  │  • set(site, keyword, data, ttl)                  │    │
│  │  • is_expired(site, keyword) → bool               │    │
│  │  • TTL per-site (default 24h)                     │    │
│  └──────────────────────────────────────────────────┘    │
│                                                           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │B2B Auth  │ │B2B Auth  │ │B2B Auth  │ │B2B Auth  │    │
│  │Crawler   │ │Crawler   │ │Crawler   │ │Crawler   │    │
│  │(form)    │ │(form)    │ │(api)     │ │(wordpress│    │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘    │
│       └────────────┼────────────┴────────────┘            │
│                    ▼                                      │
│           ┌────────────────┐                              │
│           │  BaseCrawler    │                              │
│           │  (httpx async)  │                              │
│           │  + retry        │                              │
│           │  + rate limit   │                              │
│           │  + auth hooks   │                              │
│           │  + cache hooks  │                              │
│           └────────────────┘                              │
└─────────────────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   CSV Writer                               │
│  DrugPrice[] → prices.csv                                 │
│  (append if exists, dedup by drug_name + source)          │
└─────────────────────────────────────────────────────────┘
```

### 3.2. Luồng dữ liệu (B2B Auth Sources)

```
User nhập keyword "boganic"
    │
    ▼
GUI gọi CrawlerEngine.crawl(keyword, sources[])
    │
    ▼
Engine chạy song song các crawler (asyncio.gather)
    │
    ├──► [B2B Site: Giathuoctot]
    │      │
    │      ├── CacheManager.get("giathuoctot", "boganic")
    │      │   ├── Cache hit & not expired → return cached data ✓
    │      │   └── Cache miss or expired → continue ↓
    │      │
    │      ├── AuthManager.ensure_auth("giathuoctot")
    │      │   ├── Session valid → use existing cookies
    │      │   └── Session expired/missing → login() → save new session
    │      │
    │      ├── GET search_url with session cookies
    │      │   ├── 200 OK → parse HTML/JSON → DrugPrice[]
    │      │   ├── 401 / redirect to login → re-auth → retry
    │      │   └── Network error → retry with backoff
    │      │
    │      └── CacheManager.set("giathuoctot", "boganic", data, ttl=24h)
    │
    ├──► [B2B Site: ThuocSi] → same flow as Giathuoctot
    ├──► [B2B Site: ChoThuoc247] → same flow
    └──► [B2B Site: ...] → all 9 sites in parallel
    │
    ▼
Gộp kết quả từ tất cả nguồn → CsvWriter.write(drug_prices, "prices.csv")
    │
    ▼
GUI hiển thị: "Done! N records saved from M sources."
```

### 3.3. Đóng gói portable

```
main.py + crawlers/ + gui/ + utils/
    │
    ▼  PyInstaller --onefile --windowed
    │
drug-price-crawler.exe  (~30-50MB)
    │
    ▼  Double-click trên Windows
    │
GUI hiện lên → user dùng ngay
```

---

## 4. Tech Stack

| Thành phần | Thư viện | Lý do |
|---|---|---|
| HTTP client | `httpx` | Async, HTTP/2, timeout control, cookie jar |
| HTML parser | `selectolax` | Nhanh hơn BeautifulSoup 5-10x |
| Data validation | `pydantic` | Type-safe, validate schema |
| Retry/backoff | `tenacity` | Xử lý rate limit, transient errors |
| Config | `pyyaml` | Đọc `config/accounts.yaml` |
| Cache DB | `sqlite3` (stdlib) | Cache crawl results, TTL per-site, không cần cài thêm |
| GUI | `tkinter` | Built-in Python, không cần cài thêm, nhẹ |
| CSV | `csv` (stdlib) | Đủ cho nhu cầu |
| Packaging | `pyinstaller` | Tạo .exe one-file portable |

**Không dùng** (tránh nặng):
- `playwright` / `selenium` — chỉ fallback nếu tất cả API đều khóa (thêm 150MB+ Chromium)
- `pandas` — overkill cho CSV đơn giản
- `requests` — `httpx` tốt hơn cho async

### File requirements.txt dự kiến

```
httpx[http2]==0.27.2
selectolax==0.3.21
pydantic==2.9.2
tenacity==9.0.0
pyyaml==6.0.2
pyinstaller==6.10.0   # chỉ để build
```

---

## 5. Cấu trúc thư mục

```
drug-price-crawler/
├── main.py                    # Entry point: khởi tạo GUI
├── requirements.txt
├── build.bat                  # Script build .exe cho Windows
├── build.spec                 # PyInstaller spec
├── README.md
├── .gitignore                 # Bao gồm config/accounts.yaml
│
├── config/
│   └── accounts.yaml          # ⚠️ Credentials cho 9 site B2B (KHÔNG commit)
│
├── crawlers/
│   ├── __init__.py
│   ├── base.py                # BaseCrawler: httpx client, retry, rate limit
│   ├── auth_manager.py        # AuthManager: login, session, re-auth on 401
│   ├── cache_manager.py       # CacheManager: SQLite cache with TTL per site
│   ├── b2b/                   # Crawlers cho 9 trang sỉ (cần auth)
│   │   ├── __init__.py
│   │   ├── giathuoctot.py     # GiathuoctotCrawler
│   │   ├── chothuoc247.py     # ChoThuoc247Crawler
│   │   ├── thuochapu.py       # ThuocHaPuCrawler
│   │   ├── chothuoctot.py     # ChoThuocTotCrawler
│   │   ├── thuocsi.py         # ThuocSiCrawler
│   │   ├── thuoctot3mien.py   # ThuocTot3MienCrawler
│   │   ├── thuocsisaigon.py   # ThuocSiSaiGonCrawler
│   │   ├── duocphamgiasi.py   # DuocPhamGiaSiCrawler
│   │   └── bachhoathuoc.py    # BachHoaThuocCrawler
│
├── gui/
│   ├── __init__.py
│   └── main_window.py         # MainWindow: tkinter GUI
│
├── utils/
│   ├── __init__.py
│   ├── models.py              # DrugPrice, SourceName, CSV_HEADERS
│   ├── csv_writer.py          # CsvWriter: write/append/dedup
│   ├── price_parser.py        # Parse "25.000đ" → 25000
│   └── config_loader.py       # Load config/accounts.yaml → SiteConfig objects
│
└── output/                    # Thư mục chứa CSV (auto-create)
    ├── prices.csv
    └── cache.db               # SQLite cache database (auto-create)
```

---

## 6. Chi tiết thiết kế từng Module

### 6.1. `utils/models.py` — Data Model

```python
class SourceName(Enum):
    # B2B Auth Sources (9 sites)
    GIATHUOCTOT = "Giathuoctot"
    CHOTHUOC247 = "ChoThuoc247"
    THUOCHAPU = "ThuocHaPu"
    CHOTHUOCTOT = "ChoThuocTot"
    THUOCSI = "ThuocSi"
    THUOCTOT3MIEN = "ThuocTot3Mien"
    THUOCSISAIGON = "ThuocSiSaiGon"
    DUOCPHAMGIASI = "DuocPhamGiaSi"
    BACHHOATHUOC = "BachHoaThuoc"

class DrugPrice(BaseModel):
    drug_name: str
    brand: str = ""
    manufacturer: str = ""
    dosage_form: str = ""
    strength: str = ""
    price_vnd: int           # 25000
    price_display: str       # "25.000đ"
    source: SourceName
    source_url: str
    crawled_at: datetime
```

**CSV schema**:
```
drug_name,brand,manufacturer,dosage_form,strength,price_vnd,price_display,source,source_url,crawled_at
Boganic,,Dược Hậu Giang,Viên nén,,22000,"22.000đ",Giathuoctot,https://...,2026-07-02T10:31:00
Paracetamol 500mg,,Dược phẩm XYZ,Viên nén,500mg,25000,"25.000đ",ChoThuoc247,https://...,2026-07-02T10:32:00
```

### 6.2. `crawlers/base.py` — Base Crawler

**Trách nhiệm**: cung cấp HTTP client chung, retry, rate limit cho tất cả crawler con.

**Interface**:
```python
class BaseCrawler(ABC):
    source_name: SourceName
    base_url: str
    request_delay: float = 1.0  # giây giữa mỗi request

    async def crawl(self, keyword: str) -> list[DrugPrice]:
        """Entry point: search keyword, return drug prices."""
        ...

    @abstractmethod
    async def _fetch_products(self, keyword: str) -> list[dict]:
        """Gọi API/parse HTML, trả raw data."""
        ...

    @abstractmethod
    def _parse_product(self, raw: dict) -> DrugPrice:
        """Convert 1 raw item → DrugPrice."""
        ...
```

**Tính năng**:
- `httpx.AsyncClient` với HTTP/2, timeout 30s
- `tenacity` retry: 3 lần, exponential backoff 1s→2s→4s
- User-Agent thật (Chrome desktop)
- Rate limit: `asyncio.sleep(delay)` giữa requests
- Logging: ghi log mỗi request + response status

### 6.3. `crawlers/b2b/giathuoctot.py` — Ví dụ implementation (Form Login)

```python
class GiathuoctotCrawler(BaseCrawler):
    source_name = SourceName.GIATHUOCTOT
    base_url = "https://www.giathuoctot.com"
    # Login URL & search URL lấy từ config/accounts.yaml

    async def _fetch_products(self, keyword: str) -> list[dict]:
        # 1. AuthManager.ensure_auth(site_config) → session cookies
        # 2. GET search_url với session cookies
        #    search_url = f"{base_url}/quick-order?page=1&searchTerm={keyword}"
        response = await self._client.get(
            site_config.search_url.format(keyword=keyword),
            cookies=session.cookies,
        )
        # 3. Nếu redirect về login → AuthManager.handle_auth_failure() → re-auth → retry
        # 4. Parse HTML response bằng selectolax
        tree = HTMLParser(response.text)
        products = []
        for node in tree.css(".product-item"):
            products.append({
                "name": node.css_first(".product-name").text(),
                "price": node.css_first(".price").text(),
            })
        return products

    def _parse_product(self, raw: dict) -> DrugPrice:
        return DrugPrice(
            drug_name=raw.get("name", ""),
            price_vnd=parse_price(raw.get("price", "0")),
            price_display=raw.get("price", ""),
            source=self.source_name,
            source_url=self.base_url,
        )
```

> **Lưu ý**: CSS selectors cụ thể cần xác nhận bằng DevTools trước khi code. Đây là pattern dự kiến.

### 6.4. `utils/csv_writer.py` — CSV Writer

**Trách nhiệm**: ghi/append vào CSV, không duplicate.

**Logic**:
```python
class CsvWriter:
    def write(self, prices: list[DrugPrice], filepath: str):
        # 1. Đọc CSV hiện có (nếu có)
        # 2. Tạo set key = (drug_name + source) đã có
        # 3. Filter prices: chỉ giữ những record có key mới
        #    HOẶC update price nếu key đã tồn tại (overwrite)
        # 4. Append records mới vào CSV
```

**Chính sách update**:
- **Append mode**: thêm record mới, ghi đè giá cũ nếu cùng `drug_name + source`
- Giữ lịch sử: thêm cột `crawled_at` để phân biệt lần crawl

### 6.5. `gui/main_window.py` — GUI Design

```
┌─────────────────────────────────────────────────┐
│  Drug Price Crawler                         [X] │
├─────────────────────────────────────────────────┤
│                                                  │
│  Nguồn dữ liệu:                                  │
│  ☑ Giá Thuốc Tốt (giathuoctot.com)               │
│  ☑ Chợ Thuốc 247 (chothuoc247.vn)                │
│  ☑ Thuốc Hà Phú (thuochapu.com)                  │
│  ☑ Chợ Thuốc Tốt (chothuoctot.vn)                │
│  ☑ Thuốc Sĩ (thuocsi.vn)                         │
│  ☑ Thuốc 3 Miền (thuoctot3mien.vn)               │
│  ☑ Thuốc Sĩ Sài Gòn (thuocsisaigon.vn)           │
│  ☑ Dược Phẩm Gia Sỉ (duocphamgiasi.vn)           │
│  ☑ Bách Hóa Thuốc (sales.bachhoathuoc.com)       │
│                                                  │
│  Từ khóa: [boganic                        ]      │
│                                                  │
│  File CSV: [output/prices.csv        ] [Browse] │
│                                                  │
│  [ Start Crawl ]  [ Stop ]                       │
│                                                  │
│  Progress: ████████████░░░░░░░░░  60%            │
│                                                  │
│  ┌─────────────────────────────────────────────┐│
│  │ Log:                                         ││
│  │ [10:30:01] Starting crawl for "boganic"      ││
│  │ [10:30:02] [Giathuoctot] Authenticating...   ││
│  │ [10:30:03] [Giathuoctot] Login OK, fetching  ││
│  │ [10:30:05] [Giathuoctot] Found 8 products    ││
│  │ [10:30:05] [ChoThuoc247] Cache hit (2h ago)  ││
│  │ [10:30:05] [ChoThuoc247] Found 5 products    ││
│  │ [10:30:06] [ThuocSi] Authenticating...       ││
│  │ [10:30:08] [ThuocSi] Found 12 products       ││
│  │ [10:30:08] Writing to prices.csv...          ││
│  │ [10:30:09] Done! 25 records saved.           ││
│  └─────────────────────────────────────────────┘│
└─────────────────────────────────────────────────┘
```

**Tính năng GUI**:
- Checkbox chọn nguồn (multi-select)
- Text input: từ khóa search
- File picker: chọn đường dẫn CSV
- Nút Start / Stop (cancel)
- Progress bar: % hoàn thành
- Log box: scrollable, hiển thị real-time
- Chạy crawl trong background thread (không block UI)

**Lưu ý threading**: tkinter không an toàn với asyncio. Sẽ chạy crawl trong `threading.Thread` riêng, dùng `queue.Queue` để truyền log/progress từ worker thread sang UI thread.

### 6.6. `main.py` — Entry Point

```python
def main():
    app = MainWindow()
    app.mainloop()

if __name__ == "__main__":
    main()
```

---

## 7. Đóng gói Portable Windows .exe

### 7.1. PyInstaller

```bash
pyinstaller --onefile --windowed --name "DrugPriceCrawler" \
    --icon assets/icon.ico \
    --add-data "crawlers;crawlers" \
    --add-data "gui;gui" \
    --add-data "utils;utils" \
    --add-data "config;config" \
    main.py
```

**Kết quả**: `dist/DrugPriceCrawler.exe` (~30-50MB, 1 file duy nhất)

### 7.2. `build.bat`

```bat
@echo off
echo Building Drug Price Crawler...
pip install -r requirements.txt
pyinstaller --onefile --windowed --name "DrugPriceCrawler" ^
    --add-data "crawlers;crawlers" ^
    --add-data "gui;gui" ^
    --add-data "utils;utils" ^
    --add-data "config;config" ^
    main.py
echo Done! Output: dist\DrugPriceCrawler.exe
pause
```

### 7.3. Xử lý portable

- Output CSV sẽ ghi vào cùng thư mục với .exe (`./output/prices.csv`)
- Không ghi vào registry, không cần admin
- Không cần Python cài sẵn trên máy đích

---

## 8. Rủi ro & Mitigation

| Rủi ro | Mức độ | Mitigation |
|---|---|---|
| API nhà thuốc thay đổi/break | Cao | Crawler isolated, dễ fix từng nguồn; có fallback HTML parse |
| Anti-bot / Cloudflare | Trung bình | User-Agent thật, delay 1-2s, retry backoff. Nếu khóa hoàn toàn → fallback playwright |
| Session B2B hết hạn giữa lúc crawl | Cao | AuthManager tự detect 401/redirect → re-auth → retry (max 3 lần) |
| Login form có CAPTCHA | Trung bình | Inspect DevTools trước. Nếu có CAPTCHA → cần playwright + manual solve hoặc skip site |
| Credentials bị lộ (commit git) | Cao | `.gitignore` cho `config/accounts.yaml`, không commit mật khẩu |
| Cache DB corrupt | Thấp | SQLite resilient, có WAL mode. Fallback: xóa cache.db → crawl lại |
| .exe quá nặng (>100MB) | Thấp | Tránh playwright nếu có thể; `--onefile` nén tốt |
| tkinter xấu trên Windows | Thấp | Dùng `ttk` widgets (modern theme) thay `tk` thuần |
| CSV encoding tiếng Việt | Trung bình | UTF-8 with BOM (`utf-8-sig`) để Excel đọc đúng tiếng Việt |
| Rate limit / IP ban | Trung bình | Delay giữa requests, không crawl hàng loạt, tôn trọng robots.txt |
| Duplicate records | Thấp | Dedup by `drug_name + source` trong CsvWriter |

---

## 9. Roadmap triển khai

### Phase 1: Auth Infrastructure
1. Tạo cấu trúc project + requirements.txt + `.gitignore`
2. Implement `config_loader.py` — load `accounts.yaml` → `SiteConfig` objects
3. Implement `AuthManager` — login (form + API + WordPress), session store, re-auth on 401
4. Implement `CacheManager` — SQLite cache with TTL per-site
5. Implement `BaseCrawler` — httpx + retry + rate limit + auth/cache hooks

### Phase 2: B2B Crawlers (9 sites)
6. **Research**: inspect DevTools từng trang — xác nhận login form fields, search endpoint, response format (HTML/JSON)
7. Implement crawler cho 6 trang form_login (giathuoctot, chothuoc247, thuochapu, chothuoctot, thuoctot3mien, thuocsisaigon)
8. Implement crawler cho 2 trang api_login (thuocsi, bachhoathuoc)
9. Implement crawler cho 1 trang WordPress (duocphamgiasi)
10. Test crawl: chạy CLI, verify CSV output từ 9 nguồn B2B

### Phase 3: GUI
11. Build `MainWindow` tkinter: layout, 9 source checkboxes, keyword input
12. Wire GUI ↔ CrawlerEngine: threading, progress, log (incl. auth/cache events)
13. Test end-to-end: GUI → crawl → CSV

### Phase 4: Packaging
14. `build.bat` + PyInstaller spec (include `config/` directory)
15. Test .exe trên Windows 10/11
16. Viết README hướng dẫn sử dụng

### Ước lượng effort
| Phase | Thời gian |
|---|---|
| Phase 1: Auth Infrastructure | 3-4 giờ |
| Phase 2: B2B Crawlers (9 sites) | 5-7 giờ (bao gồm research DevTools) |
| Phase 3: GUI | 2-3 giờ |
| Phase 4: Packaging | 1-2 giờ |
| **Tổng** | **11-16 giờ** |

---

## 10. Bước tiếp theo

> **Bước quan trọng nhất trước khi code**: mở DevTools (F12) trên từng trang B2B, thử đăng nhập + search, xác định:
> - Login form field names chính xác (vd: `username` vs `user` vs `email` vs `log`)
> - Có CSRF token không
> - Có CAPTCHA không
> - Search endpoint thực tế (HTML response hay JSON API)
> - Response structure (CSS selectors hoặc JSON keys cho tên thuốc + giá)

1. **Verify login endpoints** — inspect DevTools từng trang, confirm `login_url` và form fields
2. **Verify search endpoints** — đặc biệt thuocsi.vn (dashboard → tìm XHR search call)
3. **Chốt tech stack** (đã chốt: httpx + selectolax + pydantic + pyyaml + sqlite3 + tkinter + pyinstaller)
4. **Bắt đầu Phase 1** khi bạn sẵn sàng

---

*Khi bạn muốn bắt đầu implement, nói "implement" + chỉ định phase.*

# ThuocHapu.com — API Documentation

> Tài liệu phân tích network & API cho crawler. Joomla-based B2B pharmacy platform.

---

## 1. Tổng quan

| Thành phần | Giá trị |
|---|---|
| **Platform** | Joomla (PHP 8.2) + LiteSpeed + "medicine" template |
| **Auth** | Session-based (Joomla session cookie) |
| **Frontend** | Server-rendered HTML (không SPA, không AJAX cho product listing) |
| **Tổng sản phẩm** | **~840** (14 trang × 60 sản phẩm) |
| **Sản phẩm / trang** | **60** (fixed) |
| **Tổng số trang** | **14** |
| **Session hết hạn sau** | Theo config Joomla (thường 15-60 phút inactivity, cookie session browser) |
| **CSRF** | Joomla token (hidden field trong form) |
| **JSON-LD** | Có (`schema.org/Offer` với `price` trong product detail page) |

---

## 2. Authentication

### 2.1. Login Flow

**Bước 1**: GET trang login để lấy session cookie + Joomla security token

```
GET https://thuochapu.com/login.html
```

Cookies nhận được:
- `<session_id>` — Joomla session (cookie name là hash ngẫu nhiên, vd: `309e1fd73401a835b5337931259f4b30`)

HTML chứa hidden field security token:
```html
<input type="hidden" name="308402bacc59eca184bb66a3c4f19f09" value="1" />
```

**Bước 2**: POST login

```
POST https://thuochapu.com/login.html?task=user.login
Content-Type: application/x-www-form-urlencoded
Referer: https://thuochapu.com/login.html
```

**Form data:**
```
username=0388279175
password=0388279175
return=
308402bacc59eca184bb66a3c4f19f09=1
```

> **Lưu ý**: Field `username` (không phải `phone` hay `email`). Token field name là hash ngẫu nhiên — **pharse từ HTML mỗi lần login**.

**Response**: `303 See Other` → redirect đến `/`

Cookies sau login:
- `<session_id>` — updated (authenticated session)
- `joomla_user_state=logged_in` — trạng thái đăng nhập
- `joomla_remember_me_<hash>=...` — remember me (60 ngày)
- `thuochapu=1` — custom cookie (1 năm)

### 2.2. Session Management

| Cookie | Tên | Hết hạn | Ghi chú |
|---|---|---|---|
| Session | `<hash ngẫu nhiên>` | Session (browser close) | Joomla session ID |
| User state | `joomla_user_state` | Session | `logged_in` |
| Remember me | `joomla_remember_me_<hash>` | 60 ngày | Tự động login |
| Custom | `thuochapu` | 1 năm | Custom flag |

**Auth Strategy cho Crawler**:
```
1. Login → nhận session cookie
2. Mọi request gửi kèm cookies
3. Nếu response không có "Đăng xuất" → session hết hạn → re-login
4. Dùng Remember Me cookie để kéo dài session
```

### 2.3. Detect login expiry

```python
# Cách detect session hết hạn:
# 1. Response HTML không chứa "Đăng xuất" link
# 2. Response HTML chứa "Đăng nhập" form
# 3. Redirect về /login.html
```

---

## 3. Product Listing

### 3.1. Trang danh sách tất cả sản phẩm

```
GET https://thuochapu.com/danh-muc.html?start=0
Cookie: <session cookies>
```

| Param | Mô tả |
|---|---|
| `start` | Offset (0, 60, 120, 180, ..., 780). Mỗi trang 60 sản phẩm |

**Phân trang**:
```
start=0   → trang 1 (sản phẩm 1–60)
start=60  → trang 2 (sản phẩm 61–120)
start=120 → trang 3 (sản phẩm 121–180)
...
start=780 → trang 14 (sản phẩm 781–840)

Tổng: 14 trang × 60 sản phẩm = ~840 sản phẩm
```

### 3.2. HTML Structure (product listing)

Mỗi sản phẩm là **một card `div.t3-medicine`** chứa đúng 1 link `/thuoc/` và
1 `<b>` giá:

```html
<div class="t3-medicine w3-white ...">
    <div class="t3-name">
        <a href="https://thuochapu.com/thuoc/3b-phuc-vinh.html">
            Vitamin 3B Gold Phúc Vinh (Hộp 10 vỉ x 10 viên)
        </a>
    </div>
    <b>48.000</b><small class='w3-text-dark'>/Hộp</small>
</div>
```

**Parse logic (BẮT BUỘC duyệt theo card)**:
```python
# Duyệt từng div.t3-medicine → mỗi card: <a href=".../thuoc/..."> = name+URL,
# <b>XX.XXX</b> đầu tiên trong card = price, <small>/Unit</small> = unit.
```

> ⚠️ **KHÔNG gom toàn bộ `<a>` + toàn bộ `<b>` rồi zip 1-1 theo document-order.**
> Trang có node rác `<b>2646</b>` (bộ đếm tổng sản phẩm ở header) khớp regex
> giá → lọt vào đầu danh sách và **đẩy lệch TOÀN BỘ giá đi 1 ô** (mọi sản phẩm
> nhận nhầm giá của sản phẩm khác). Bug này từng khiến Alaxan hiển thị 2.646đ
> thay vì 110.000đ. Duyệt theo card `.t3-medicine` loại bỏ hẳn rủi ro vì `<b>`
> rác nằm ngoài mọi card. (fix 2026-07-20 — `crawlers/b2b/thuochapu.py`)

### 3.3. Search

```
GET https://thuochapu.com/search.html?filter_search=boganic
Cookie: <session cookies>
```

**Response**: HTML với "Danh sách N thuốc đã được tìm thấy cho từ khóa '...'"

> **Lưu ý**: Search không có pagination — trả tất cả kết quả trong 1 trang.

> ⚠️ **`filter_search` BỊ BỎ QUA (CRITICAL, xác nhận sống 2026-07-20)**: dù truyền
> keyword gì, server vẫn trả **nguyên trang đầu catalog** (60 sp đầu theo alphabet,
> bắt đầu bằng "Vitamin 3B..."). KHÔNG dùng search để tra giá theo tên. Hệ quả từng
> gặp: GUI search "Alaxan" nhận trang đầu → lọc theo product_id không khớp → fallback
> lấy tất cả → **mọi sản phẩm hiện chung giá 48.000** (giá sp đầu). Vì vậy crawler đặt
> `keyword_search_supported = False`: CLI crawl toàn catalog rồi lọc tại chỗ; GUI (chọn
> 1 sp) gọi `fetch_price_by_id(url)` đọc giá từ JSON-LD trang chi tiết (§3.4). Lưu ý
> JSON-LD có xuống dòng THÔ trong `description` → phải `json.loads(..., strict=False)`.

### 3.4. Product Detail Page

```
GET https://thuochapu.com/thuoc/{slug}.html
Cookie: <session cookies>
```

**JSON-LD structured data** (trong HTML):
```html
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Boganic Forte (5 vỉ x 10 viên nang)",
  "offers": {
    "@type": "Offer",
    "price": "96000",
    "priceCurrency": "VND",
    "priceValidUntil": "02-07-2028"
  }
}
</script>
```

→ Có thể parse JSON-LD để lấy giá chính xác từ product detail page.

---

## 4. Product Data Structure

### 4.1. Fields từ listing page

| Field | Cách lấy | Ví dụ |
|---|---|---|
| **name** | `<a>` text content | Vitamin 3B Gold Phúc Vinh (Hộp 10 vỉ x 10 viên) |
| **url** | `<a href="...">` | https://thuochapu.com/thuoc/3b-phuc-vinh.html |
| **slug** | Từ URL | 3b-phuc-vinh |
| **price** | `<b>XX.XXX</b>` → parse | 48000 |
| **price_display** | `<b>XX.XXX</b>` text | 48.000 |
| **unit** | `<small>/XX</small>` | Hộp |

### 4.2. Fields từ detail page (JSON-LD)

| Field | JSON-LD path | Ví dụ |
|---|---|---|
| **name** | `Product.name` | Boganic Forte (5 vỉ x 10 viên nang) |
| **price** | `Product.offers.price` | 96000 |
| **currency** | `Product.offers.priceCurrency` | VND |
| **price_valid_until** | `Product.offers.priceValidUntil` | 02-07-2028 |

### 4.3. Mapping sang DrugPrice model

```python
# Từ listing page:
DrugPrice(
    drug_name     = product_name,       # "Vitamin 3B Gold Phúc Vinh (Hộp 10 vỉ x 10 viên)"
    brand         = "",
    manufacturer  = "",
    dosage_form   = "",
    strength      = "",
    price_vnd     = parsed_price,       # 48000
    price_display = price_text,         # "48.000đ"
    source        = SourceName.THUOCHAPU,
    source_url    = product_url,        # "https://thuochapu.com/thuoc/3b-phuc-vinh.html"
    crawled_at    = datetime.now(),
)
```

---

## 5. Categories

| Tên | URL |
|---|---|
| Thuốc tân dược | `/danh-muc.html` (default) |
| Thực phẩm chức năng | Category filter |
| Mỹ phẩm | Category filter |
| Thiết bị y tế | Category filter |
| Shop Bao Cao su | Category filter |
| Thuốc tăng giá | Promotion category |
| Thuốc giảm giá | Promotion category |

---

## 6. Crawl Strategy

### 6.1. Crawl ALL

```python
# Pseudocode
total_pages = 14
all_products = []

for page in range(total_pages):
    start = page * 60
    url = f"https://thuochapu.com/danh-muc.html?start={start}"

    html = GET(url, cookies=session_cookies)

    # Parse HTML: tìm tất cả <a href="...thuoc/...html">
    # Parse price: <b>XX.XXX</b> trong div kế tiếp
    products = parse_product_listing(html)
    all_products.extend(products)

    sleep(2)  # rate limit

# Result: ~840 products in 14 requests (~30 seconds)
```

### 6.2. Crawl với search

```python
# Search bằng keyword
url = "https://thuochapu.com/search.html?filter_search=boganic"
html = GET(url, cookies=session_cookies)
# Parse: "Danh sách N thuốc đã được tìm thấy"
# Không có pagination — tất cả kết quả trong 1 trang
```

### 6.3. HTML Parse Strategy

```python
# Listing page parse — DUYỆT THEO CARD (xem cảnh báo §3.2):
from selectolax.parser import HTMLParser

tree = HTMLParser(html)
products = []

for card in tree.css('div.t3-medicine'):
    a = card.css_first('a[href*="/thuoc/"]')
    if a is None:
        continue
    name = a.text(strip=True)
    url = a.attributes.get('href', '')

    price_node = card.css_first('b')          # <b> nằm TRONG card → không lệch
    price_vnd = int(price_node.text(strip=True).replace('.', '')) if price_node else 0

    unit_node = card.css_first('small')
    unit = unit_node.text(strip=True).replace('/', '') if unit_node else ""

    products.append({'name': name, 'price': price_vnd, 'url': url, 'unit': unit})
```

### 6.4. Cache & Token refresh

```python
# Cache:
# - Cache key: thuochapu:all_products
# - TTL: 24 giờ
# - Cache value: list[DrugPrice] + crawled_at

# Token refresh:
# 1. GET /danh-muc.html với cookies
# 2. Nếu HTML chứa "Đăng nhập" form → session hết hạn
# 3. Re-login: POST /login.html?task=user.login
# 4. Extract Joomla security token từ login page HTML mỗi lần
```

---

## 7. Headers chuẩn

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...
Cookie: <session_hash>=...; joomla_user_state=logged_in
Referer: https://thuochapu.com/danh-muc.html
```

---

## 8. Lưu ý

> **Joomla Security Token**: Hidden field name là hash ngẫu nhiên (vd: `308402bacc59eca184bb66a3c4f19f09`). **Phải parse từ HTML mỗi lần login** — không hardcode.

> **Session cookie name**: Cookie name là hash ngẫu nhiên (vd: `309e1fd73401a835b5337931259f4b30`). Không cố định như `PHPSESSID`.

> **HTML-only**: Không có JSON API. Tất cả data trong HTML — phải parse HTML bằng `selectolax` hoặc `BeautifulSoup`.

> **Price format**: `48.000` (dấu chấm phân cách nghìn). Parse: `int("48.000".replace('.', ''))` → `48000`.

> **No pagination on search**: Search trả tất cả kết quả trong 1 trang. Listing page (`/danh-muc.html`) có pagination `?start=N`.

> **JSON-LD**: Product detail page có JSON-LD schema.org data với giá chính xác. Dùng `<script type="application/ld+json">` để parse.

> **Login URL**: `/login.html` (không phải `/dang-nhap.html` — URL đó trả 404).

> **Product URL pattern**: `/thuoc/{slug}.html` — slug là tên sản phẩm không dấu, cách bằng dấu gạch ngang.

> **Rate limit**: Chưa thấy 429. Delay 2s giữa requests để an toàn. 14 requests cho toàn bộ catalog = ~30 giây.

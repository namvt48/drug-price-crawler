# DuocPhamGiaSi.vn — API Documentation

> WordPress + WooCommerce. HTML-only, no REST API enabled.

---

## 1. Tổng quan

| Thành phần | Giá trị |
|---|---|
| **Platform** | WordPress + WooCommerce |
| **Server** | Apache/2.4.58 (Ubuntu) |
| **Auth** | WordPress session cookie + nonce |
| **Login plugin** | Meta Box User Profile (`mbup-form`) |
| **Image CDN** | i0.wp.com (Jetpack/WordPress.com) |
| **Theme** | `duoc-pham` (custom) |
| **Plugins** | `shopduocpham`, `meta-box-aio`, `blaze-slider` |
| **WooCommerce REST API** | Không khả dụng (404) |
| **Login URL** | `/tai-khoan` |

---

## 2. Authentication

### 2.1. Login

```
POST https://duocphamgiasi.vn/tai-khoan
Content-Type: application/x-www-form-urlencoded
Referer: https://duocphamgiasi.vn/tai-khoan
```

**Form data:**
```
mbup_key=<key từ HTML>
mbup_type=login
nonce_rwmb-user-login=<nonce từ HTML>
_wp_http_referer=/tai-khoan
user_login=0388279175
user_pass=0388279175
```

| Field | Mô tả |
|---|---|
| `mbup_key` | Meta Box form key (parse từ HTML) |
| `mbup_type` | `login` |
| `nonce_rwmb-user-login` | WordPress nonce (parse từ HTML) |
| `user_login` | Số điện thoại |
| `user_pass` | Mật khẩu |

**Response**: `302 Found` → redirect

Cookies sau login:
- `wordpress_sec_*` — WordPress auth cookie
- `wordpress_logged_in_*` — WordPress logged-in cookie

### 2.2. Token Extraction

```python
# Parse từ /tai-khoan HTML:
# nonce: <input name="nonce_rwmb-user-login" value="2426a4c9e0" />
# key: <input name="mbup_key" value="eb90202ac69f3fb8a0e580324155e45b" />
```

---

## 3. Product Listing

### 3.1. Search

```
GET https://duocphamgiasi.vn/?post_type=product&s=boganic
Cookie: wordpress_sec_*; wordpress_logged_in_*
```

**Response**: HTML (85KB) — WooCommerce product listing, cần parse HTML.

### 3.2. WooCommerce REST API (không khả dụng)

```
GET https://duocphamgiasi.vn/wp-json/wc/v3/products?search=boganic
→ 404 "Không tìm thấy đường dẫn nào phù hợp"
```

REST API không được bật trên site này.

### 3.3. HTML Parse (đã sửa lại — xác nhận sống 2026-07-11)

> ⚠️ Selector WooCommerce mặc định bên dưới (`li.product`/`div.product`) **KHÔNG
> còn khớp** — theme site đã đổi sang class tuỳ biến, khiến parser trả về 0 sản
> phẩm IM LẶNG (không lỗi) suốt — không phải do site không bán sản phẩm tìm kiếm.

```python
# Cấu trúc HTML thật (theme tuỳ biến, xác nhận sống):
# Card sản phẩm: <article class="product-item">
#   <div class="product-card" data-price="64000">   <!-- GIÁ ở attribute này -->
#     <div class="entry-title"><a href="...">Tên sản phẩm</a></div>
#   </div>
# </article>
# data-price="0" khi chưa login (giá ẩn) — khớp khi đã login.
# Text ".price"/".woocommerce-Price-amount" THƯỜNG RỖNG — không dùng để lấy giá,
# dùng attribute `data-price` (đáng tin hơn, JS cart cũng dùng field này).
```

### 3.4. Catalog toàn bộ (không keyword) — xác nhận sống 2026-07-11

> ⚠️ `/shop/` (URL cũ) trả **404** — không phải catalog thật của site này.

```
GET https://duocphamgiasi.vn/product/              # trang 1
GET https://duocphamgiasi.vn/product/page/{n}/      # trang sau (pretty-permalink)
```

Page size xác nhận = 15. **Lưu ý phân trang khác nhau theo chế độ**: search
(`?s=...`) dùng query param `?paged=N` cho trang sau; catalog (`/product/`,
không `s=`) dùng pretty-permalink `/product/page/N/` — dùng nhầm kiểu sẽ lặp lại
trang 1 vô hạn (param `page=N` cũ hoàn toàn không có tác dụng ở cả 2 chế độ).

---

## 4. Crawl Strategy

```python
# 1. GET /tai-khoan → extract nonce + mbup_key
# 2. POST /tai-khoan → login → nhận cookies
# 3. GET /?post_type=product&s= → search products (HTML)
# 4. Hoặc GET /shop/ → all products listing (HTML)
# 5. Parse HTML bằng selectolax
```

---

## 5. Headers chuẩn

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...
Cookie: wordpress_sec_*; wordpress_logged_in_*
Referer: https://duocphamgiasi.vn/tai-khoan
Content-Type: application/x-www-form-urlencoded
```

---

## 6. Lưu ý

> **No REST API**: WooCommerce REST API không được bật. Phải parse HTML.

> **Login field**: `user_login` (SĐT) + `user_pass`. Không phải `username` hay `email`.

> **Nonce**: WordPress nonce thay đổi mỗi session. Parse từ HTML mỗi lần login.

> **Meta Box plugin**: Login form dùng plugin Meta Box User Profile, không phải WordPress login mặc định.

> **Jetpack**: Images qua i0.wp.com (Jetpack CDN). Có thể resize qua URL params `?w=480`.

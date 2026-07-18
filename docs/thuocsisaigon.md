# ThuocSiSaiGon.vn — API Documentation

> Haravan e-commerce platform + ASP.NET Core backend. HTML-only, no JSON API.

---

## 1. Tổng quan

| Thành phần | Giá trị |
|---|---|
| **Platform** | Haravan (Vietnamese e-commerce SaaS) |
| **Backend** | ASP.NET Core (Antiforgery token) |
| **Server** | openresty (nginx) |
| **CDN** | hstatic.net (Haravan CDN) |
| **Auth** | Session cookie (`customer_sig`) + ASP.NET Antiforgery |
| **Frontend** | Server-rendered HTML + jQuery |
| **Product URL** | `/products/{slug}` |
| **Login URL** | Form trên homepage (popup/modal) |

---

## 2. Authentication

### 2.1. Login

```
POST https://thuocsisaigon.vn/account/login
Content-Type: application/x-www-form-urlencoded
Referer: https://thuocsisaigon.vn/
```

**Form data:**
```
form_type=customer_login
utf8=✓
__RequestVerificationToken=<token từ HTML>
customer[email]=tvket2012@gmail.com
customer[password]=123456
```

> **Field names**: `customer[email]` và `customer[password]` (có bracket).

> **Token**: `__RequestVerificationToken` — ASP.NET Core anti-forgery token. Parse từ HTML hidden input.

**Response**: `302 Found` → redirect đến `/account`

Cookies sau login:
- `customer_sig` — auth cookie (Haravan session)
- `.AspNetCore.Antiforgery.*` — CSRF cookie

### 2.2. Token Extraction

```python
# Lấy token từ homepage HTML:
# <input name='__RequestVerificationToken' type='hidden' value='CfDJ8...'>
```

### 2.3. Detect login expiry

```python
# Response redirect về /account/login → session hết hạn
# Response HTML chứa "Đăng nhập" form → chưa login
```

---

## 3. Product Listing

### 3.1. Search

```
GET https://thuocsisaigon.vn/search?type=product&q=filter=(title:product contains boganic)
Cookie: customer_sig=...
```

**Response**: HTML (781KB) — server-rendered, cần parse HTML.

### 3.2. HTML Parse

```python
# Product links: <a href="/products/{slug}">
# Prices: trong HTML elements (cần inspect specific CSS selectors)
# Parse bằng selectolax hoặc BeautifulSoup
```

### 3.3. Categories (từ homepage)

Sản phẩm được tổ chức theo danh mục với URL `/products/{slug}`.

### 3.4. Catalog toàn bộ (không keyword) — xác nhận sống 2026-07-11

> ⚠️ `/search?q=` (rỗng, không lọc) trả **0 sản phẩm** — endpoint search của
> Haravan không hỗ trợ "browse tất cả", không phải lỗi crawler.

```
GET https://thuocsisaigon.vn/collections/all?page=1
```

Quy ước chuẩn Haravan/Shopify cho "toàn bộ sản phẩm". Page size xác nhận = 32,
`?page=N` hoạt động thật (page 1 ≠ page 2). Parse cùng selector với `/search`
(`a[href*="/products/"]`).

---

## 4. Crawl Strategy

```python
# 1. GET homepage → extract __RequestVerificationToken
# 2. POST /account/login → nhận cookies
# 3. GET /search?type=product&q=filter=... → parse HTML
# 4. Hoặc GET /collections/all → parse all products
```

---

## 5. Headers chuẩn

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...
Cookie: customer_sig=...; .AspNetCore.Antiforgery.*=...
Referer: https://thuocsisaigon.vn/
Content-Type: application/x-www-form-urlencoded
```

---

## 6. Lưu ý

> **Haravan platform**: Không có JSON API. Tất cả data trong HTML.

> **Field names**: `customer[email]` và `customer[password]` (có bracket `[]`).

> **Token**: `__RequestVerificationToken` — ASP.NET Core, khác với CSRF token thông thường.

> **Login page**: Không có trang login riêng. Form nằm trong homepage HTML (popup/modal).

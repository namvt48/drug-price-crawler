# ChoThuoc247.vn — API Documentation

> Tài liệu phân tích network & API cho crawler. Laravel-based B2B pharmacy platform.

---

## 1. Tổng quan

| Thành phần | Giá trị |
|---|---|
| **Platform** | Laravel (PHP) + jQuery + nginx |
| **Auth** | Session-based (cookie `the_thao_product_session`) + CSRF token |
| **Frontend** | Server-rendered HTML + jQuery AJAX |
| **Tổng sản phẩm** | **3,120** |
| **Sản phẩm / trang** | **18** (fixed, không thay đổi được) |
| **Tổng số trang** | **174** (3120 / 18) |
| **Session hết hạn sau** | **7 ngày** (604800 giây) |
| **CSRF** | Có (`_token` field + `XSRF-TOKEN` cookie) |

---

## 2. Authentication

### 2.1. Login Flow

**Bước 1**: GET trang login để lấy CSRF token + cookies

```
GET https://chothuoc247.vn/dang-nhap.html
```

Trích `_token` từ HTML:
```html
<meta name="csrf-token" content="YdMWyoIJlkzU4MWeSjl89sDtXIv75MAzU8yIgsHO" />
```

Cookies nhận được:
- `XSRF-TOKEN` — CSRF cookie (Laravel)
- `the_thao_product_session` — Session cookie (HttpOnly)

**Bước 2**: POST login với `_token` + credentials

```
POST https://chothuoc247.vn/submitLoginCustomer
Content-Type: application/x-www-form-urlencoded
```

**Form data:**
```
_token=YdMWyoIJlkzU4MWeSjl89sDtXIv75MAzU8yIgsHO
phone=0388279175
password=0388279175
```

**Response**: `302 Found` → redirect đến `https://chothuoc247.vn/dat-hang.html`

→ Login thành công. Cookies mới được set (session authenticated).

> **Lưu ý**: Field name là `phone`, không phải `username` hay `email`.

### 2.2. Session Management

| Cookie | Tên | Hết hạn | HttpOnly |
|---|---|---|---|
| Session | `the_thao_product_session` | 7 ngày | Có |
| CSRF | `XSRF-TOKEN` | 7 ngày | Không |

**Auth Strategy cho Crawler**:
```
1. Login → nhận session cookie (7 ngày)
2. Mọi request gửi kèm cookies
3. Nếu redirect về /dang-nhap.html → session hết hạn → re-login
```

### 2.3. CSRF Token

Mọi POST request phải kèm `_token` trong form data. Token lấy từ:
- `<meta name="csrf-token" content="..." />` trong HTML
- Hoặc `<input type="hidden" name="_token" value="..." />` trong form

Token thay đổi mỗi khi session làm mới. Lấy token mới bằng cách GET trang `dat-hang.html`.

---

## 3. Product API

### 3.1. Search Products (AJAX endpoint)

```
POST https://chothuoc247.vn/searchProduct
Content-Type: application/x-www-form-urlencoded
X-Requested-With: XMLHttpRequest
Referer: https://chothuoc247.vn/dat-hang.html
Cookie: <session cookies>
```

**Form data:**
```
_token=<csrf_token>
page=1
search=boganic
producerId=
categoryId=
```

| Param | Type | Required | Mô tả |
|---|---|---|---|
| `_token` | string | Có | CSRF token từ meta tag |
| `page` | int | Có | Số trang (bắt đầu từ 1) |
| `search` | string | Có | Từ khóa tìm kiếm. Rỗng = tất cả |
| `producerId` | int | Không | Filter theo nhà sản xuất |
| `categoryId` | int | Không | Filter theo danh mục |

### 3.2. Response Format

```json
{
  "data": [
    {
      "id": 5534,
      "name": "Boganic Nén Bao Đường Traphaco (H/100V)",
      "common_name": "Boganic Nén",
      "image": "image/Boganic đường1624549234.jpeg",
      "price": 67000,
      "silver_price": 67000,
      "gold_price": 66500,
      "diamond_price": 66000,
      "suggest_web_price": 68000,
      "unit": "Hộp",
      "web_volume": "Hộp 5 vỉ x 20 viên",
      "status": "OK",
      "web_stock": "HAVE",
      "category_id": 31,
      "producer_id": 104,
      "min_invent": 8,
      "product_type": "COM",
      "product_classify": "thuoc_khong_ke_don",
      "can_order": true,
      "date_expire": null,
      "created_at": "2021-06-20 13:02:53",
      "updated_at": "2026-06-26 15:15:17"
    }
  ],
  "totalPages": 174,
  "totalItems": 3120,
  "currentPage": 1,
  "orderProduct": [...],
  "categories": [...]
}
```

### 3.3. Pagination

| Field | Mô tả |
|---|---|
| `totalPages` | Tổng số trang (174) |
| `totalItems` | Tổng số sản phẩm (3120) |
| `currentPage` | Trang hiện tại |
| `data.length` | Sản phẩm / trang = **18** (fixed) |

```
Page 1 → sản phẩm 1–18
Page 2 → sản phẩm 19–36
...
Page 174 → sản phẩm 3121–3120

Tổng: 174 requests × 18 items = 3,120 sản phẩm
```

### 3.4. Các endpoint AJAX khác

| Mục đích | Method | Endpoint | Params |
|---|---|---|---|
| **Search products** | POST | `/searchProduct` | `page, search, producerId, categoryId` |
| **Add to cart** | POST | `/addProductToCart` | `id, quantity` |
| **Update quantity** | POST | `/updateQuantityProduct` | `id, quantity` |
| **Get cart** | POST | `/getListProductInCart` | — |
| **Get cart total** | POST | `/getTotalInOrder` | — |
| **Remove out of stock** | POST | `/removeOutOfStockProducts` | `product_ids` |

---

## 4. Product Data Structure

### 4.1. Fields quan trọng

| Field | Type | Mô tả |
|---|---|---|
| `id` | int | Product ID |
| `name` | string | Tên sản phẩm đầy đủ |
| `common_name` | string | Tên chung |
| `price` | float | **Giá sỉ cơ bản** |
| `silver_price` | float | Giá hạng Silver |
| `gold_price` | float | Giá hạng Gold |
| `diamond_price` | float | Giá hạng Diamond |
| `suggest_web_price` | float | Giá đề xuất bán web |
| `unit` | string | Đơn vị (Hộp, Lọ, Tuýp) |
| `web_volume` | string | Quy cách đóng gói |
| `status` | string | "OK" = hoạt động |
| `web_stock` | string | "HAVE" = còn hàng |
| `category_id` | int | ID danh mục |
| `producer_id` | int | ID nhà sản xuất |
| `min_invent` | int | Tồn kho tối thiểu |
| `product_type` | string | "COM" = thương mại |
| `product_classify` | string | Phân loại (thuoc_khong_ke_don, etc.) |
| `can_order` | bool | Có thể đặt hàng |
| `image` | string | Path ảnh (relative) |
| `date_expire` | string | Hạn sử dụng |
| `updated_at` | string | Ngày cập nhật |

### 4.2. Hệ thống giá đa tầng

Site có **4 mức giá** theo hạng khách hàng:

| Mức | Field | Ví dụ (Boganic Nén) |
|---|---|---|
| Cơ bản | `price` | 67,000 |
| Silver | `silver_price` | 67,000 |
| Gold | `gold_price` | 66,500 |
| Diamond | `diamond_price` | 66,000 |
| Đề xuất web | `suggest_web_price` | 68,000 |

### 4.3. Mapping sang DrugPrice model

```python
DrugPrice(
    drug_name     = product["name"],
    brand         = "",
    manufacturer  = "",  # cần lookup producer_id
    dosage_form   = product.get("unit", ""),
    strength      = product.get("web_volume", ""),
    price_vnd     = product["price"],
    price_display = f"{product['price']:,}đ",
    source        = SourceName.CHOTHUOC247,
    source_url    = f"https://chothuoc247.vn/san-pham/{product['id']}",
    crawled_at    = datetime.now(),
)
```

---

## 5. Categories

5 danh mục trả về trong response:

| ID | Tên |
|---|---|
| 27 | Giảm đau, hạ sốt, chống viêm |
| 29 | Thực phẩm chức năng |
| 33 | Vitamin và khoáng chất |
| 37 | Hô hấp (ho, cảm cúm, cảm lạnh..) |
| 38 | Mỹ phẩm & làm đẹp |

Filter: `categoryId=27` trong form data.

---

## 6. Crawl Strategy

### 6.1. Crawl ALL

```python
# Pseudocode
total_pages = 174
all_products = []

for page in range(1, total_pages + 1):
    response = POST searchProduct
    data = {
        "_token": csrf_token,
        "page": page,
        "search": "",
        "producerId": "",
        "categoryId": ""
    }

    products = response["data"]
    all_products.extend(products)

    sleep(2)  # rate limit

# Result: 3,120 products in 174 requests (~6 minutes)
```

### 6.2. Crawl với whitelist

```python
# Theo keyword
data = {"_token": token, "page": 1, "search": "boganic", ...}

# Theo category
data = {"_token": token, "page": 1, "search": "", "categoryId": 27, ...}

# Theo manufacturer
data = {"_token": token, "page": 1, "search": "", "producerId": 104, ...}
```

### 6.3. Token refresh

```python
# CSRF token hết hạn khi session hết hạn
# Detect: response 419 (CSRF mismatch) hoặc redirect về /dang-nhap.html

# Refresh flow:
# 1. GET /dat-hang.html với cookies
# 2. Nếu redirect → session hết hạn → re-login
# 3. Nếu 200 → extract _token mới từ HTML meta tag
# 4. Dùng token mới cho searchProduct
```

---

## 7. Headers chuẩn

```
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ...
X-Requested-With: XMLHttpRequest
Referer: https://chothuoc247.vn/dat-hang.html
Cookie: XSRF-TOKEN=...; the_thao_product_session=...
Content-Type: application/x-www-form-urlencoded
```

---

## 8. Lưu ý

> **CSRF**: Mọi POST phải kèm `_token` trong form data. Không gửi token → HTTP 419.

> **Page size fixed**: 18 sản phẩm/trang, không thay đổi được. Không có param `pageSize` hay `limit`.

> **Giá đa tầng**: Có 4 mức giá (price, silver, gold, diamond). Mức `price` là giá cơ bản.

> **Session 7 ngày**: Cookie hết hạn sau 7 ngày. Detect hết hạn: redirect về `/dang-nhap.html`.

> **Image path**: Relative path (`image/Boganic đường1624549234.jpeg`). Full URL: `https://chothuoc247.vn/image/...`

> **Rate limit**: Chưa thấy 429. Nên giữ delay 2s giữa requests.

> **Search fuzzy**: Tìm "boganic" trả về cả sản phẩm liên quan (Biogaia, Bổ Gan Abipha, etc.) — không chỉ exact match.

# ThuocTot3Mien.vn — API Documentation

> Next.js App Router + Laravel API backend. Token-based auth.

---

## 1. Tổng quan

| Thành phần | Giá trị |
|---|---|
| **Frontend** | Next.js (App Router, Turbopack, React) |
| **CSS** | Tailwind CSS v4 |
| **Backend API** | `https://api.thuoctot3mien.vn/api/web/v1` |
| **Auth** | Token-based (`accessToken` trong localStorage) |
| **Cloud** | Cloudflare + Wasabi S3 |
| **Image storage** | `https://s3.ap-southeast-1.wasabisys.com/duocdaphuc-cdn` |
| **Payment** | MegaPay (`pg.megapay.vn`) |
| **Login** | `/dang-nhap` (client-side rendered form) |

---

## 2. Authentication

### 2.1. Login (đã sửa lại — xác nhận sống 2026-07-11)

> ⚠️ **Endpoint/field bên dưới đúng, nhưng THIẾU 1 điều kiện bắt buộc**: backend đòi
> header `Origin`/`Referer` khớp domain thật. Thiếu 2 header này → **luôn** trả 401
> "Tài khoản hoặc mật khẩu không chính xác" **dù mật khẩu đúng** (thông báo lỗi cố
> tình mơ hồ, không lộ lý do thật — chống bot). Test trực tiếp cùng 1 request, chỉ
> thêm 2 header → login OK ngay. Đây là nguyên nhân crawler báo "sai mật khẩu" suốt
> dù user xác nhận login tay được bình thường.

```
POST https://api.thuoctot3mien.vn/api/web/v1/customer/login
Content-Type: application/json
Origin: https://thuoctot3mien.vn
Referer: https://thuoctot3mien.vn/dang-nhap
```

**Request body:**
```json
{
  "email": "0388279175",
  "password": "0388279175"
}
```

> **Field `email`**: Dù field tên `email`, giá trị là số điện thoại. Field `phone` và `username` trả về 422 (validation error).

**Response (thành công):**
```json
{
  "status": "success",
  "message": "Đăng nhập người dùng thành công.",
  "data": {
    "message": "Customer login successful",
    "customer": {...},
    "token": "23069|7SQFhrMIiRzk30R5cf1RWLpw...",
    "expires_at": "..."
  },
  "server_time": "..."
}
```

> **Token nằm ở `data.token`** (không phải `data.accessToken` như ghi trước đó).
> Cũng có route Next.js proxy cùng chức năng (`POST /api/auth/login` trên chính
> `thuoctot3mien.vn`, không cần Origin/Referer vì same-origin) nhưng token nó trả
> **không dùng được** để gọi thẳng `api.thuoctot3mien.vn` — không dùng route này,
> dùng thẳng backend + Origin/Referer như trên.

**Response (sai credentials thật, đã có Origin/Referer):** HTTP 401
```json
{"status":"error","message":"Tài khoản hoặc mật khẩu không chính xác."}
```

### 2.2. Next.js Auth Routes

| Route | Method | Mô tả |
|---|---|---|
| `/api/auth/login` | POST | Next.js Route Handler (proxy đến API backend) |
| `/api/auth/logout` | POST | Logout (clear session) |

### 2.3. Token Storage

```javascript
// From JS code:
ACCESS_TOKEN_KEY = "accessToken"
AUTH_STORAGE_KEY  = "auth_storage"
STORAGE_TOKEN     = "storage_token"

// Token lưu trong localStorage
// Mọi API request cần header: Authorization: Bearer <accessToken>
```

### 2.4. Auth Endpoints

| Endpoint | Method | Mô tả | Auth |
|---|---|---|---|
| `/customer/login` | POST | Login | Không |
| `/customer/register` | POST | Đăng ký | Không |
| `/customer/reset-password` | POST | Quên mật khẩu | Không |
| `/customer/profiles` | GET | Thông tin tài khoản | Có |
| `/customer/coin-balance` | GET | Số dư xu | Có |

---

## 3. Product API

### 3.1. Product List (đã xác nhận sống 2026-07-11)

```
GET https://api.thuoctot3mien.vn/api/web/v1/products?page=1&limit=20&search=boganic
Authorization: Bearer <token>
Origin: https://thuoctot3mien.vn
Referer: https://thuoctot3mien.vn/dang-nhap
```

| Param | Type | Mô tả |
|---|---|---|
| `page` | int | Số trang |
| `limit` | int | Số sản phẩm / trang |
| `search` | string | Từ khóa tìm kiếm |

> ⚠️ **Catalog toàn bộ (không keyword)**: gửi `search=""` (rỗng) trả **0 sản
> phẩm** — server không coi "rỗng" = "không lọc". Phải **bỏ hẳn field `search`**
> khỏi query string khi không có từ khóa (không gửi `search=""`).

**Response (chưa auth):** HTTP 401
```json
{"message":"Unauthenticated."}
```

**Response item (thật, rút gọn) — CHÚ Ý giá là OBJECT lồng, không phải số phẳng:**
```json
{
  "id": 741, "name": "Boganic KD (5 Vỉ/ Hộp) - Traphaco",
  "packaging": "Hộp 5 vỉ x 20 viên",
  "base_price": "65000.00",
  "price": {"base": 65000, "final": 65000, "is_flash_sale": false, "discount_amount": 0},
  "unit": {"id": 2, "uuid": "...", "name": "Hộp"},
  "unit_id": 2, "manufacturer_id": 116
}
```

> **Field `price` là object** `{base, final, ...}` — dùng `price.final` (giá sau
> giảm giá/flash-sale nếu có), fallback `price.base`, rồi fallback `base_price`
> (chuỗi thập phân `"65000.00"` — **ép về `float()` trước khi parse**, vì hàm
> parse-số kiểu VN sẽ đọc nhầm phần thập phân thành nghìn: "65000.00" → 6.500.000).
> **Field `unit` là object quan hệ** (`{id, uuid, name}`), không phải chuỗi — dùng
> `packaging` (chuỗi) để lấy quy cách đóng gói, không dùng `unit`.
> Không có field tên nhà sản xuất dạng chuỗi (chỉ có `manufacturer_id` dạng số).

### 3.2. Product Detail

```
GET https://api.thuoctot3mien.vn/api/web/v1/products/{id}
Authorization: Bearer <accessToken>
```

### 3.3. Product Suggestion

```
GET https://api.thuoctot3mien.vn/api/web/v1/productSuggestion
Authorization: Bearer <accessToken>
```

### 3.4. Page Blocks

```
GET https://api.thuoctot3mien.vn/api/web/v1/page-blocks
```

### 3.5. Sliders

```
GET https://api.thuoctot3mien.vn/api/web/v1/sliders
```

### 3.6. Settings (không cần auth)

```
GET https://api.thuoctot3mien.vn/api/web/v1/settings
```

**Response (200 OK):**
```json
{
  "status": "success",
  "data": [
    {
      "id": 1,
      "group_name": "customer_auto_deactivate",
      "setting_key": "config",
      "value": {"enabled": true, "re_active_days": 90, "no_transaction_days": 90}
    },
    {
      "id": 2,
      "group_name": "bank_transfer_dis...",
      ...
    }
  ]
}
```

### 3.7. Customer Product Activity

```
POST https://api.thuoctot3mien.vn/api/web/v1/customer-product-activity
Authorization: Bearer <accessToken>
```

---

## 4. Routes (Frontend)

| Route | API mapping |
|---|---|
| `/san-pham` | `/products` |
| `/dat-hang-nhanh` | `/quick-order` |
| `/dang-nhap` | Login form |
| `/account` | `/customer/profiles` |
| `/account-order` | Order history |
| `/account-profile` | Profile |
| `/account-password` | Change password |
| `/account-quay-so` | Lucky draw |
| `/account-rank` | Customer rank |

---

## 5. Tất cả API endpoints

| Endpoint | Method | Auth | Mô tả |
|---|---|---|---|
| `/customer/login` | POST | Không | Login (field: `email` + `password`) |
| `/customer/register` | POST | Không | Đăng ký |
| `/customer/reset-password` | POST | Không | Quên mật khẩu |
| `/customer/profiles` | GET | Có | Thông tin tài khoản |
| `/customer/coin-balance` | GET | Có | Số dư xu |
| `/products` | GET | Có | Danh sách sản phẩm |
| `/products/{id}` | GET | Có | Chi tiết sản phẩm |
| `/productSuggestion` | GET | Có | Gợi ý sản phẩm |
| `/page-blocks` | GET | Không | Page blocks |
| `/sliders` | GET | Không | Sliders |
| `/settings` | GET | Không | Cấu hình hệ thống |
| `/customer-product-activity` | POST | Có | Activity tracking |

> **API base**: `https://api.thuoctot3mien.vn/api/web/v1`
> Tất cả endpoints ở trên được prefix với base URL này.

---

## 6. Crawl Strategy

### 6.1. Flow

```python
# 1. Login
POST https://api.thuoctot3mien.vn/api/web/v1/customer/login
Body: {"email": "0388279175", "password": "0388279175"}
→ Receive accessToken

# 2. Get all products
GET https://api.thuoctot3mien.vn/api/web/v1/products?page=1&limit=20
Headers: Authorization: Bearer <accessToken>
→ Pagination: page + limit

# 3. Search
GET https://api.thuoctot3mien.vn/api/web/v1/products?page=1&limit=20&search=boganic
Headers: Authorization: Bearer <accessToken>
```

### 6.2. Token Management

```python
# Token lưu trong localStorage key "accessToken"
# Detect expired: response 401 "Unauthenticated."
# Refresh: re-login
```

---

## 7. Headers chuẩn

```
Content-Type: application/json
Accept: application/json
Authorization: Bearer <accessToken>
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...
```

---

## 8. Lưu ý

> **Login field**: Dùng `email` (không phải `phone` hay `username`). Giá trị là SĐT.

> **Auth required**: `/products` cần auth token. `/settings`, `/page-blocks`, `/sliders` không cần.

> **Image storage**: Wasabi S3 (`s3.ap-southeast-1.wasabisys.com/duocdaphuc-cdn`). Không phải AWS S3.

> **Turbopack**: Site dùng Turbopack (thay vì webpack) cho Next.js build.

> **Payment**: MegaPay (`pg.megapay.vn`).

> **ENV**: `NEXT_PUBLIC_PRODUCT_SEARCH_TRACKING_ENABLED` — tracking product search.

# ChoThuocTot.vn — API Documentation

> Next.js App Router + Medlink API backend. Token-based auth. Confirmed via Playwright.

---

## 1. Tổng quan

| Thành phần | Giá trị |
|---|---|
| **Frontend** | Next.js 14+ (App Router, React Server Components) |
| **State** | Redux Toolkit (auth slice, product slice) |
| **UI** | Ant Design |
| **Backend API** | `https://api.medlink.vn/pharmacy/` |
| **Auth** | Token-based (`access_token` trong Redux state) |
| **Cloud** | Cloudflare |
| **Login page** | `/auth/login` |
| **Product page** | `/san-pham` |

> ✅ **Confirmed bằng Playwright**: API backend là `api.medlink.vn` (Medlink platform).

---

## 2. Authentication

### 2.1. Login Page

```
URL: https://chothuoctot.vn/auth/login
```

Login form (Ant Design):
- **username**: Số điện thoại (`input[name="username"]`, placeholder: "Nhập số điện thoại!")
- **password**: Mật khẩu (`input[type="password"]`, placeholder: "Nhập mật khẩu!")

### 2.2. Routes

| Route | Mô tả |
|---|---|
| `/auth/login` | Đăng nhập |
| `/auth/register` | Đăng ký |
| `/auth/forgot-password` | Quên mật khẩu |
| `/account/account-info` | Thông tin tài khoản |
| `/account/order-history` | Lịch sử đơn hàng |
| `/account/change-password` | Đổi mật khẩu |
| `/account/business-profile` | Hồ sơ doanh nghiệp |

---

## 3. Product API (Confirmed via Playwright)

### 3.1. Product Search

```
GET https://api.medlink.vn/pharmacy/supply/search-product?page=1&size=10&product_name=boganic&company_id=0&pinned=false&report_top_selling=true
Authorization: Bearer <access_token>
```

| Param | Type | Mô tả |
|---|---|---|
| `page` | int | Số trang |
| `size` | int | Số sản phẩm / trang |
| `product_name` | string | Từ khóa tìm kiếm (rỗng = tất cả) |
| `company_id` | int | ID công ty (0 = tất cả) |
| `product_type` | string | Loại sản phẩm |
| `business_type` | string | Loại kinh doanh |
| `pinned` | bool | Sản phẩm ghim |
| `report_*` | bool | Các flag báo cáo |

**Response (chưa auth):** HTTP 401
```json
{"error":"unauthorized","error_description":"Full authentication is required to access this resource"}
```

**Response item (thật, rút gọn) — xác nhận sống 2026-07-11:**
```json
{
  "drug_id": 1664313, "drg_drug_name": "Boganic Forte Traphaco (H/50v)",
  "company_name": "Traphaco - Công ty cổ phần TRAPHACO", "package_desc": "Hộp 5 vỉ x 10 viên nang mềm",
  "units": [{"price": 105400.0, "wholesale_price": 105400.0}]
}
```

> ⚠️ **Field parse trước đây SAI** (`crawlers/b2b/chothuoctot.py` dùng `product_name`/`name`,
> `price`, `unit`) — API thật không có các field đó ở top-level, nên `drug_name`
> luôn rỗng và MỌI kết quả bị `crawl()` lọc mất im lặng (không lỗi, chỉ trả 0 sản
> phẩm) dù request/login hoàn toàn đúng. Field thật: tên ở `drg_drug_name`, id ở
> `drug_id`, quy cách ở `package_desc`, NSX ở `company_name`. **Giá nằm trong
> `units[0].wholesale_price`** (mảng — 1 sản phẩm có thể nhiều đơn vị tính).

### 3.2. Banners (không cần auth)

```
GET https://api.medlink.vn/pharmacy/supply/supplier/banner?page=1&size=20&status=1&banner_type=PROMOTION,POLICY,NEWS,INFO
```

### 3.3. Supplier List (không cần auth)

```
GET https://api.medlink.vn/pharmacy/supply/supplier?company_id=0&company_type=COMPANY&page=1&size=100
```

### 3.4. News (không cần auth)

```
GET https://api.medlink.vn/pharmacy/news?page=1&size=20&status=1&news_type=ABOUT
```

---

## 4. Tất cả API endpoints (Confirmed)

| Endpoint | Method | Auth | Mô tả |
|---|---|---|---|
| `/pharmacy/supply/search-product` | GET | Có | Tìm kiếm sản phẩm |
| `/pharmacy/supply/supplier` | GET | Không | Danh sách nhà cung cấp |
| `/pharmacy/supply/supplier/banner` | GET | Không | Banners |
| `/pharmacy/news` | GET | Không | Tin tức |

> **API base**: `https://api.medlink.vn`

---

## 5. Crawl Strategy

```python
# 1. Login tại /auth/login (cần Playwright do Ant Design modal)
#    Field: username (SĐT) + password
#    Token lưu trong Redux state + cookie

# 2. Search products
GET https://api.medlink.vn/pharmacy/supply/search-product?page=1&size=20&product_name=boganic
Headers: Authorization: Bearer <access_token>

# 3. Get all (empty product_name)
GET https://api.medlink.vn/pharmacy/supply/search-product?page=1&size=20&product_name=
```

---

## 6. Lưu ý

> **Medlink API**: Backend là `api.medlink.vn` — nền tảng Medlink.

> **Auth required**: `/search-product` cần auth token. Banners, news, suppliers không cần.

> **Login**: Ant Design form với modal overlay. Cần Playwright để tự động login. API login URL chưa capture được do login fail trong Playwright.

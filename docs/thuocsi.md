# ThuocSi.vn — API Documentation

> Next.js Pages Router + Buymed backend. Confirmed via Playwright.

---

## 1. Tổng quan

| Thành phần | Giá trị |
|---|---|
| **Platform** | Next.js Pages Router (có `__NEXT_DATA__`) |
| **UI** | MUI (Material UI) |
| **State** | Zustand |
| **Company** | Buymed |
| **API Gateway** | `https://thuocsi.vn/backend/` |
| **Auth** | Basic Auth (`isBasic=true`) + Bearer token (`isAuth=true`) |
| **Basic Auth** | `PARTNER/v2.frontend.web:6MuwVTk4QwUdewhP` |
| **CDN** | `cdn-web-next.thuocsi.vn`, `cdn-gcs.thuocsi.vn` |
| **Login** | Modal popup tại `/?login=true` |

> ✅ **Confirmed bằng Playwright**: API base là `https://thuocsi.vn/backend/` với Basic Auth.

---

## 2. Authentication

### 2.1. Auth Types

Site có 3 loại auth:

| Type | Header | Dùng cho |
|---|---|---|
| `isBasic=true` | `Authorization: Basic <base64>` | Endpoints công khai (config, master data) |
| `isAuth=true` | `Authorization: Bearer <token>` | Endpoints cần login |
| `isAuth=false` | Không auth | Login, register |

**Basic Auth credentials** (confirmed):
```
Username: PARTNER/v2.frontend.web
Password: 6MuwVTk4QwUdewhP
Base64: UEFSVE5FUi92Mi5mcm9udGVuZC53ZWI6Nk11d1ZUazRRd1VkZXdoUD
```

### 2.2. Login Flow (đã sửa lại — xác nhận sống 2026-07-11)

> ⚠️ Bản ghi cũ ở đây (`POST /authentication` với `{phone, password, deviceId}`) **SAI**
> — vừa sai path vừa sai tên field, luôn trả lỗi dù mật khẩu đúng. Đã reverse-engineer
> lại trực tiếp từ JS bundle production (`_app-*.js`, module export `d="/marketplace/customer/v1"`).

```
POST https://thuocsi.vn/backend/marketplace/customer/v1/authentication
Content-Type: application/json
Authorization: Basic UEFSVE5FUi92Mi5mcm9udGVuZC53ZWI6Nk11d1ZUazRRd1VkZXdoUA==

Body: {"username": "0989872266", "password": "...", "type": "CUSTOMER", "deviceId": "<uuid>"}
```

**Response** (200):
```json
{"status":"OK","data":[{"bearerToken":"...","expiredTime":"...","type":"CUSTOMER","username":"0989872266"}],"message":"Logged in successfully"}
```

> **Lưu ý**: field là `username` (không phải `phone`), bắt buộc có `type: "CUSTOMER"`.
> Token nằm ở `data[0].bearerToken` (không phải `token`/`accessToken`).

---

## 3. Product API (Confirmed via Playwright)

### 3.1. Endpoints đã capture

| Endpoint | Method | Auth | Mô tả |
|---|---|---|---|
| `/backend/core/config-manager/v1/app-value/single?appCode=884FW961&isBasic=true` | GET | Basic | Cấu hình app |
| `/backend/marketplace/customer/v1/lead-source?isActive=true&isBasic=true` | GET | Basic | Nguồn khách hàng |
| `/backend/core/master-data/v1/districts?provinceCode=&isBasic=true` | GET | Basic | Quận/huyện |
| `/backend/integration/chat/v1/configuration/list` | POST | Bearer | Cấu hình chat |
| `/backend/marketplace/frontend-apis/v2/seller/product/skip-login?isBasic=true` | GET | Basic | Seller products (skip login) |

### 3.2. Product List (đã sửa lại — xác nhận sống 2026-07-11)

> ⚠️ Path cũ `/screen/product/list` (thiếu tiền tố) trả 404. Path thật có tiền tố
> `/marketplace/frontend-apis/v2` (biến `h` trong cùng module JS).

```
POST https://thuocsi.vn/backend/marketplace/frontend-apis/v2/screen/product/list
Authorization: Bearer <token>
Content-Type: application/json

Body: {
  "offset": 0,
  "limit": 20,
  "filter": {},
  "text": "",
  "isAvailable": true,
  "queryOption": {}
}
```

**Response — giá bị MÃ HOÁ, không trả thô:**
```json
{"status":"OK","message":"Search product successfully.","total":17997,
 "data":[{"productName":"Boganic forte traphaco (h/50v)","productId":1483,
  "skuCode":"ROTYACBVI4.TRA-BOG-003","slug":"rotyacbvi4-boganic-forte-traphaco-h50v",
  "priceEncrypted":"qJQz2fvMuHcK8qfOC/dQAA==","discountPriceEncrypted":"m8iMfLtZDPqu96hsvbu6Aw==",
  "sellerName":"...","volume":"Hộp 5 vỉ x 10 viên nang mềm"}]}
```

> ⚠️ **Field `total` xác nhận catalog thật = 17.997 sản phẩm** (khớp "10.000+"
> ghi ở §6). **1 trang GIỮA catalog có thể trả THIẾU so với `limit`** (vd
> offset=20 chỉ trả 19/20 sản phẩm dù `total` báo còn rất nhiều — xác nhận sống
> 2026-07-11, lặp lại nhiều lần, không phải ngẫu nhiên 1 lần). Dùng
> `len(batch) < limit` để biết "hết trang" là **SAI** — sẽ dừng rất sớm (bug thật
> đã gặp: catalog chỉ lấy được 39/17.997 sản phẩm). Phải dùng `offset + len(batch)
> >= total` để biết chính xác khi nào dừng — xem `crawlers/b2b/thuocsi.py::_total_of`.
> Full-catalog crawl (`keyword=""`) ở tốc độ `delay_seconds` hiện tại (5s) mất
> ~900 trang × 5s ≈ **75 phút** — cân nhắc khi chạy catalog refresh.

### 3.2b. Giải mã giá (AES-CBC, key hardcode trong JS bundle)

`priceEncrypted`/`discountPriceEncrypted` là base64 của ciphertext AES-CBC, **key = IV**
(cùng 1 giá trị), suy từ chuỗi bí mật hardcode trong bundle production (`_app-*.js`,
export `YU`): **`"thu0c21.v4@2023?buym3d"`**.

Thuật toán suy khoá (16 byte, y hệt hàm `R(e)` trong JS):
1. `n = sum(ord(ch) << 10 for ch in seed)` (số nguyên lớn, không mask).
2. Lấy chuỗi thập phân của `n`, mỗi ký tự số → 1 byte (charCode).
3. Pad về đủ 16 byte bằng giá trị `127` (nếu thiếu) hoặc cắt lấy 16 byte đầu (nếu dư).

Giải mã: base64-decode `value` → AES-CBC decrypt (`key=IV=key derived ở trên`) →
UTF-8 decode → `.strip()` (padding là khoảng trắng) → parse số.

Đã xác nhận: giải mã `priceEncrypted`="qJQz2fvMuHcK8qfOC/dQAA==" → `106900`, khớp
`discountPriceEncrypted` giải mã ra `100400` với `discountPercent:6` (106900×0.94≈100486,
sai số nhỏ do làm tròn phía server). Implement tại `crawlers/b2b/thuocsi.py::_decrypt_price`.

### 3.3. Search (Fuzzy)

```
POST https://thuocsi.vn/backend/search/fuzzy
Authorization: Bearer <token>

Body: {text: "boganic", isGetPriceAfterVoucher: true, isGetSKUReplace: true}
```

### 3.4. Tất cả API paths (từ JS)

| Path | Method | Mô tả |
|---|---|---|
| `/authentication` | POST | Login |
| `/password-recovery` | POST | Quên mật khẩu |
| `/password` | POST | Đổi mật khẩu |
| `/me` | GET | Thông tin user |
| `/screen/product/list` | POST | Danh sách sản phẩm |
| `/product/list` | GET | Sản phẩm theo IDs |
| `/product/detail-encrypted` | GET | Chi tiết (encrypted) |
| `/product/detail-raw` | GET | Chi tiết (raw) |
| `/product/category/list` | GET | Danh mục |
| `/search/fuzzy` | POST | Tìm kiếm mờ |
| `/search/fuzzy-encrypted` | POST | Tìm kiếm (encrypted) |
| `/search/fuzzy/lite` | POST | Tìm kiếm (lite) |
| `/account` | GET | Tài khoản |
| `/users/my-voucher` | GET | Voucher |
| `/users/loyalty_points` | GET | Điểm thưởng |

---

## 4. Crawl Strategy

```python
# 1. Login
POST https://thuocsi.vn/backend/authentication
Body: {"phone": "0989872266", "password": "0989872266", "deviceId": "<unique_id>"}
→ Receive access_token

# 2. Get all products
POST https://thuocsi.vn/backend/screen/product/list
Headers: Authorization: Bearer <token>
Body: {"offset": 0, "limit": 20, "isAvailable": true}

# 3. Search
POST https://thuocsi.vn/backend/search/fuzzy
Headers: Authorization: Bearer <token>
Body: {"text": "boganic"}
```

---

## 5. Headers chuẩn

```
Content-Type: application/json
Accept: application/json
# For basic endpoints:
Authorization: Basic UEFSVE5FUi92Mi5mcm9udGVuZC53ZWI6Nk11d1ZUazRRd1VkZXdoUD
# For authed endpoints:
Authorization: Bearer <access_token>
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...
```

---

## 6. Lưu ý

> **Basic Auth**: Dùng cho endpoints `isBasic=true`. Credentials: `PARTNER/v2.frontend.web:6MuwVTk4QwUdewhP`.

> **DeviceId**: Login yêu cầu `deviceId` trong body.

> **10,000+ products**: Meta description ghi "10000+ sản phẩm chính hãng".

# Sales.BachHoaThuoc.com — API Documentation

> Next.js + Teko APIs platform. OAuth 2.0 auth. Confirmed via Playwright.

---

## 1. Tổng quan

| Thành phần | Giá trị |
|---|---|
| **Platform** | Next.js (Pages Router) + Teko |
| **CDN** | `shopfront-cdn.tekoapis.com` |
| **Auth** | OAuth 2.0 (Authorization Code + PKCE) |
| **OAuth Domain** | `oauth.bachhoathuoc.com` |
| **Identity API** | `identity.tekoapis.com` |
| **User API** | `users.tekoapis.com` |
| **Product API** | `discovery.tekoapis.com` |
| **Cart API** | `carts-consumer.tekoapis.com` |
| **Search API** | `search.tekoapis.com` |
| **Notification** | `stn.tekoapis.com` |
| **Tracking** | `footprint-ingestor.tekoapis.com` |
| **Client ID** | `555a7a17030d471da7f7d6a5029318e5` |
| **Terminal** | `350_OLN_WEB_0001` |
| **Platform ID** | `21` |
| **Build ID** | `Zl74ismMT1rO1wveT8UUN` |

> ✅ **Confirmed bằng Playwright**: Full OAuth flow + product API đã capture.

---

## 2. Authentication (Confirmed)

### 2.1. OAuth 2.0 Flow (Authorization Code + PKCE)

**Step 1**: User navigates to login → redirect to Teko Identity

```
GET https://identity.teko.vn/login?challenge=<challenge>&state=<state>
```

**Step 2**: Submit credentials

```
POST https://identity.tekoapis.com/api/v1/users/login
Content-Type: application/json

Body: {
  "challenge": "<challenge>",
  "username": "0388279175",
  "password": "0388279175",
  "client_id": "555a7a17030d471da7f7d6a5029318e5"
}
```

**Step 3**: Exchange code for token

```
POST https://oauth.bachhoathuoc.com/oauth/token
Content-Type: application/x-www-form-urlencoded

Body: code=<code>&grant_type=authorization_code&redirect_uri=https://sales.bachhoathuoc.com&client_id=555a7a17030d471da7f7d6a5029318e5&code_verifier=<verifier>
```

**Response**:
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIs...",
  "token_type": "Bearer",
  "expires_in": 43200
}
```

- **Algorithm**: RS256 (RSA + SHA-256)
- **Token expiry**: ~15 hours (43200 seconds)
- **User ID**: `2538a989bd074055b2ed7b66c802e377`

### 2.2. User Profile

```
GET https://users.tekoapis.com/profiles?platformId=21
Authorization: Bearer <access_token>
```

**Response**:
```json
{
  "code": "USI000S",
  "message": "Success",
  "result": {
    "profile": {
      "userId": "2538a989bd074055b2ed7b66c802e377",
      "name": "Nhà thuốc Thúy Thúy - Lào Cai",
      "telephone": "0388279175",
      "approvalStatus": "approved",
      "clientCode": "350_0001"
    }
  }
}
```

---

## 3. Product API (Confirmed)

### 3.1. Category Product Listing

```
POST https://discovery.tekoapis.com/api/v2/search-skus-v2
Authorization: Bearer <access_token>
Content-Type: application/json

Body: {
  "terminalId": 289,
  "page": 1,
  "pageSize": 40,
  "slug": "/c/thuoc-ke-don",
  "filter": {},
  "sorting": {
    "sort": "SORT_BY_UNSPECIFIED",
    "order": "ORDER_BY_UNSPECIFIED"
  },
  "returnFilterable": [],
  "isNeedFeaturedProducts": true,
  "userId": "2538a989bd074055b2ed7b66c802e377"
}
```

| Param | Mô tả |
|---|---|
| `terminalId` | 289 (fixed for web) |
| `page` | Số trang |
| `pageSize` | Số sản phẩm / trang (max 40) |
| `slug` | Category slug (vd: `/c/thuoc-ke-don`) |
| `filter` | Filter conditions |
| `sorting` | Sort options |
| `userId` | User ID từ login |

**Response**:
```json
{
  "code": 200,
  "message": "Success",
  "data": {
    "products": [
      {
        "sku": "220900029",
        "name": "KĐ.Daehwa Almetamin ...",
        "imageUrl": "https://lh3.googleusercontent.com/...",
        "uomName": "Hộp",
        "brandName": "...",
        "canonical": "almetamin-hq-hop-10-vi-x-10-vien-dae-hwa-korea--s220900029"
      }
    ]
  }
}
```

### 3.2. Product Details by SKU

```
GET https://discovery.tekoapis.com/api/v1/products?skus=220901799,230104341&terminalCode=350_OLN_WEB_0001
Authorization: Bearer <access_token>
```

| Param | Mô tả |
|---|---|
| `skus` | Comma-separated SKU IDs |
| `terminalCode` | `350_OLN_WEB_0001` (fixed for web) |

**Response**:
```json
{
  "code": "0",
  "result": {
    "products": [
      {
        "productInfo": {
          "sku": "230104341",
          "name": "Contussin New Siro Ho (Hộp 1 lọ x 100ml).Danapha",
          "imageUrl": "https://lh3.googleusercontent.com/...",
          "brand": {"code": "khac", "name": "Khác"},
          "categories": [{"code": "C0006", "name": "Thuốc kê đơn"}],
          "seller": {...}
        }
      }
    ]
  }
}
```

### 3.3. Categories

| Category | Slug |
|---|---|
| Hàng chương trình | `/c/hang-chuong-trinh` |
| Sale hàng CLC | `/c/sale-hang-clc` |
| Sản phẩm giá tốt | `/c/san-pham-gia-tot` |
| Sản phẩm bán chạy | `/c/san-pham-ban-chay` |
| Thuốc kê đơn | `/c/thuoc-ke-don` |
| Thuốc không kê đơn | `/c/thuoc-khong-ke-don` |
| Dược mỹ phẩm | `/c/duoc-my-pham` |
| Thảo dược và thực vật | `/c/thao-duoc-va-thuc-vat` |
| Thực phẩm chức năng | `/c/thuc-pham-chuc-nang` |

> ⚠️ **Danh sách này KHÔNG đầy đủ** (xác nhận sống 2026-07-11) — nav thật (lấy từ
> `pageProps.menu` trong `__NEXT_DATA__` trang chủ) còn có `/c/dung-cu-y-te`,
> `/c/san-pham-khuyen-mai`, `/c/xa-hang`. Quan trọng hơn: **category không phải
> dimension đầy đủ** — nhiều sản phẩm không gắn category nào. Chia catalog theo
> 9-12 category chỉ phủ được **7.416/10.439 (~71%)**. Dùng §3.3b bên dưới thay thế.

### 3.3b. Chia catalog theo BRAND (khuyến nghị — đầy đủ hơn category)

Field `filter.brands` của `search-skus-v2` lọc thật (giống `slug`, khác `keyword`
bị bỏ qua). Lấy danh sách brand thật từ `pageProps.serverFilters.brands` trên bất
kỳ trang category nào (`GET /c/<slug>`, đọc `__NEXT_DATA__`).

**Xác nhận sống 2026-07-11**: tổng 44 brand = **10.520** sản phẩm (raw, trước khử
trùng) — sát với tổng catalog thật (10.439), phủ đầy đủ hơn NHIỀU so với 9-12
category (7.416, ~71%).

```
POST https://discovery.tekoapis.com/api/v2/search-skus-v2
Body: {..., "filter": {"brands": ["oem"]}, "keyword": "", ...}   # KHÔNG cần slug
```

**Brand "oem" một mình vượt cap `page*pageSize<=5000`** (6.816 sản phẩm) — chia
nhỏ thêm bằng khoảng giá (`filter.priceGte`/`priceLte`, mọi sản phẩm đều có giá —
dimension đầy đủ hơn category để sub-partition):
```json
{"filter": {"brands": ["oem"], "priceGte": "90000", "priceLte": "130000"}}
```
Ghi chú: category KHÔNG dùng được để chia nhỏ "oem" (test sống: 9 category × oem
chỉ cộng dồn 5.548/6.816 — thiếu ~1.268, xác nhận category không đầy đủ ở mọi cấp
độ). Chia theo giá (11 khoảng, xem `_OEM_PRICE_BUCKETS` trong
`crawlers/b2b/bachhoathuoc.py`) cho ~92% riêng phần "oem" — có thể còn sót nhỏ do
biên khoảng giá/sản phẩm giá null, chấp nhận được so với bỏ hẳn "oem".

> ⚠️ **Rủi ro rate-limit**: việc dò tìm category/brand qua nhiều lần login liên
> tục (mỗi crawler instance mới = 1 lần OAuth PKCE đầy đủ) đã từng gây **HTTP 429
> "Too Many Requests" ngay ở bước login** (xác nhận sống 2026-07-11) sau khoảng
> 15-20 phút probe dồn dập. Khi crawl thật, dùng ĐÚNG 1 phiên đăng nhập cho toàn
> bộ `crawl_all()` (đã đúng trong code hiện tại) — không tạo crawler instance mới
> cho mỗi brand/category.

### 3.4. Cart

```
GET https://carts-consumer.tekoapis.com/api/v2/carts?terminal=350_OLN_WEB_0001
Authorization: Bearer <access_token>
```

---

## 4. Tất cả API endpoints (Confirmed)

| Endpoint | Method | Auth | Mô tả |
|---|---|---|---|
| `identity.tekoapis.com/api/v1/users/login` | POST | Không | Login |
| `oauth.bachhoathuoc.com/oauth/token` | POST | Code | Exchange token |
| `users.tekoapis.com/profiles?platformId=21` | GET | Bearer | User profile |
| `discovery.tekoapis.com/api/v2/search-skus-v2` | POST | Bearer | Product listing by category |
| `discovery.tekoapis.com/api/v1/products?skus=...` | GET | Bearer | Product details by SKU |
| `carts-consumer.tekoapis.com/api/v2/carts` | GET | Bearer | Cart |
| `search.tekoapis.com/api/v1/banner/search` | POST | Không | Banners |
| `stn.tekoapis.com/api/notifications` | GET | Bearer | Notifications |
| `user-privacy-policy.tekoapis.com/api/v1/users/policies` | GET | Bearer | Privacy policies |
| `loyalty-consumer-bff.tekoapis.com/api/v1/network-config` | GET | Bearer | Loyalty config |

---

## 5. Crawl Strategy

```python
# 1. Login via OAuth (cần Playwright do PKCE flow)
#    Playwright mở /login → điền SĐT + mật khẩu → submit
#    Capture access_token từ response

# 2. Get products by category
POST https://discovery.tekoapis.com/api/v2/search-skus-v2
Headers: Authorization: Bearer <access_token>
Body: {
  "terminalId": 289,
  "page": 1,
  "pageSize": 40,
  "slug": "/c/thuoc-ke-don",
  "filter": {},
  "sorting": {"sort": "SORT_BY_UNSPECIFIED", "order": "ORDER_BY_UNSPECIFIED"},
  "isNeedFeaturedProducts": true,
  "userId": "<user_id>"
}

# 3. Get product details
GET https://discovery.tekoapis.com/api/v1/products?skus=<sku1>,<sku2>&terminalCode=350_OLN_WEB_0001
Headers: Authorization: Bearer <access_token>
```

---

## 6. Headers chuẩn

```
Authorization: Bearer <access_token>
Content-Type: application/json
Accept: application/json
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...
```

---

## 7. Lưu ý

> **OAuth + PKCE**: Login qua OAuth 2.0 với PKCE. Cần Playwright để xử lý redirect flow.

> **Token expiry**: ~15 giờ (43200 giây). Cần refresh hoặc re-login.

> **Terminal**: `350_OLN_WEB_0001` (web), terminalId: `289`.

> **Teko Platform**: Backend hoàn toàn trên Teko APIs (`*.tekoapis.com`).

> **Product data**: Product listing qua `search-skus-v2`, details qua `products?skus=`. Pagination: `page` + `pageSize` (max 40).

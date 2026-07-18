# Giathuoctot.com — API Documentation

> Tài liệu phân tích network & API cho crawler. Dựa trên phân tích thực tế Angular SPA + backend API.

---

## 1. Tổng quan

| Thành phần | Giá trị |
|---|---|
| **Frontend** | Angular SPA (single-page application) |
| **Backend API** | `https://api.giathuoctot.com/` |
| **Auth** | JWT Token (Bearer header) |
| **Cloud** | Cloudflare (CDN/WAF) + AWS S3 (`ecrms-appfiles`) |
| **Tổng sản phẩm** | **8,250** (thời điểm phân tích) |
| **Max limit/request** | **200** sản phẩm |
| **Số requests crawl all** | **42** (8250 / 200) |
| **Token hết hạn sau** | **25 giờ** (90000 giây) |
| **Refresh token hết hạn** | **~3 ngày** (259200 giây) |
| **reCAPTCHA** | Có siteKey nhưng **không yêu cầu khi login** |

---

## 2. Authentication

### 2.1. Login

```
POST https://api.giathuoctot.com/authentication/account/v2/login
Content-Type: application/json
```

**Request body:**
```json
{
  "userName": "0388279175",
  "password": "0388279175"
}
```

**Response (200 OK):**
```json
{
  "data": {
    "userName": "0388279175",
    "fullName": "0989872266",
    "jwtToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refreshToken": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "expiresIn": 90000,
    "expiresInSeconds": 90000
  },
  "isSuccess": true,
  "message": "Đăng nhập thành công"
}
```

**Token info:**
- `jwtToken`: Dùng trong header `Authorization: Bearer <token>` cho mọi API call
- `expiresInSeconds`: 90000 = **25 giờ**
- Token lưu trong cookie tên `token` trên frontend

### 2.2. Refresh Token

```
POST https://api.giathuoctot.com/authentication/account/refresh-token
Content-Type: application/json
```

**Request body:**
```json
{
  "refreshToken": "<refreshToken từ login>"
}
```

Dùng khi jwtToken hết hạn (sau 25h). Refresh token sống ~3 ngày. Nếu refresh fail → re-login.

### 2.3. Auth Strategy cho Crawler

```
┌─────────────────────────────────────────────────┐
│  1. Check cached token                          │
│     ├── Token còn hạn (< 25h) → dùng tiếp       │
│     └── Token hết hạn → refresh-token           │
│           ├── Refresh OK → dùng token mới       │
│           └── Refresh fail → re-login           │
│                                                  │
│  2. Gọi API với Authorization: Bearer <token>   │
│     ├── 200 OK → xử lý data                     │
│     └── 401 Unauthorized → re-login → retry     │
└─────────────────────────────────────────────────┘
```

---

## 3. Product API

### 3.1. Lấy toàn bộ sản phẩm (crawl ALL)

```
POST https://api.giathuoctot.com/product/product/retrieve-products-client
Content-Type: application/json
Authorization: Bearer <jwtToken>
from: Web
source: FE
```

**Request body:**
```json
{
  "limit": 200,
  "offset": 0
}
```

| Param | Type | Required | Mô tả |
|---|---|---|---|
| `limit` | int | Có | Số sản phẩm / request. **Max 200** |
| `offset` | int | Có | Vị trí bắt đầu. `0` = từ đầu, `200` = từ sản phẩm 201 |
| `searchTerm` | string | Không | Filter theo từ khóa. Rỗng = tất cả |
| `categoryId` | string | Không | Filter theo category ID |

**Response:**
```json
{
  "total": 8250,
  "products": [ ... ]
}
```

### 3.2. Phân trang

```
Offset 0    → sản phẩm 1–200
Offset 200  → sản phẩm 201–400
Offset 400  → sản phẩm 401–600
...
Offset 8200 → sản phẩm 8201–8250 (50 sản phẩm cuối)

Tổng: ceil(8250 / 200) = 42 requests
```

### 3.3. Các endpoint khác

| Mục đích | Method | Endpoint | Auth | Body |
|---|---|---|---|---|
| **Retrieve all (auth)** | POST | `product/product/retrieve-products-client` | Có | `{limit, offset}` |
| **Retrieve all (guest)** | POST | `product/product/retrieve-products-guest` | Không | `{limit, offset}` |
| **Retrieve all (member)** | POST | `product/product/retrieve-products-member` | Có | `{limit, offset}` |
| **Search (auth)** | POST | `product/product/search-products-client` | Có | `{limit, offset, searchTerm}` |
| **Search (guest)** | POST | `product/product/search-products-guest` | Không | `{limit, offset, searchTerm}` |
| **Search (member)** | POST | `product/product/search-products-member` | Có | `{limit, offset, searchTerm}` |
| **Get by ID** | GET | `product/product/{id}` | Không | — |
| **Get by slug** | GET | `product/product/slug/{slug}` | Không | — |
| **New products** | GET | `product/product/get-new-product` | Không | — |
| **Promotion products** | POST | `product/product/get-promotion-products` | Có | body |
| **Suggestion** | POST | `product/product/retrieve-products-suggestion` | Có | body |
| **Categories** | GET | `product/category` | Không | — |
| **Product group categories** | GET | `configuration/configurationV2/retrieve-product-group-categories` | Không | — |
| **Manufacturers** | POST | `product/product/retrieve-manufacturer` | Có | body |

> **Quan trọng:** `retrieve-products-client` và `search-products-client` trả về **giá** (`basePrice`, `pricingTablePrice`). Các phiên bản `-guest` trả về `basePrice = 0` (ẩn giá).

### 3.4. So sánh Guest vs Auth

| Sản phẩm | Guest `basePrice` | Auth `basePrice` |
|---|---|---|
| Boganic Forte | 0 | 111,500 |
| Boganic 20 viên nén | 0 | 77,600 |
| Boganic siro 100ml | 0 | 47,100 |
| Boganic Premium | 0 | 271,500 |

→ **Phải dùng `retrieve-products-client` với JWT token** để lấy giá.

---

## 4. Product Data Structure

### 4.1. Tất cả fields

```json
{
  "id": "6a1d34b2727f7855a6482102",
  "productID": 62952,
  "name": "Bogana Detox L-Arginine 200mg bổ gan hộp 6 vỉ x 10 viên nang Gia Phát",
  "searchName": "bogana detox l-arginine 200mg bo gan hop 6 vi x 10 vien nang gia phat",
  "categories": [
    {
      "categoryId": "69cbb6268b778de53d8604c7",
      "name": "Nhóm SP độc quyền",
      "slug": "nhom-sp-doc-quyen"
    }
  ],
  "dosageForm": 0,
  "imageUrls": [
    "https://api.giathuoctot.com/filestorage/filestorage/get-image?token=..."
  ],
  "ingredients": [
    {"name": "Alpha Chymotrypsin", "volume": "4200 IU"}
  ],
  "manufacturer": {
    "code": "Traphaco",
    "name": "Traphaco"
  },
  "registrationNumber": "",
  "currentPrice": 0,
  "basePrice": 140000,
  "pricingTablePrice": 140000,
  "retailUnit": "Hộp",
  "retailUnitQuantityPerUnit": 0,
  "stockStatus": 1,
  "slug": "bogana-detox-l-arginine-200mg-...-SP264346850",
  "sku": "SP264346850",
  "productInfo": [
    {"name": "Danh mục", "value": "Nhóm thuốc", "type": 0, "display": true},
    {"name": "Dạng bào chế", "value": "252", "type": 1, "display": true},
    {"name": "Quy cách", "value": "252", "type": 2, "display": true},
    {"name": "Xuất xứ thương hiệu", "value": null, "type": 3, "display": true},
    {"name": "Nhà sản xuất", "value": "...", "type": 4, "display": true}
  ],
  "wholesalePrices": [...],
  "tieredPrices": [...],
  "quantityAvailable": 0,
  "unit": "...",
  "usage": "...",
  "volume": "...",
  "weight": 0,
  "origin": null,
  "labels": [...],
  "tags": [...],
  "updatedDate": "2026-06-18T07:...",
  "hasPrice": true,
  "isCombo": false,
  "comboItems": []
}
```

### 4.2. Fields quan trọng cho crawler

| Field | Type | Mô tả | Có giá trị khi |
|---|---|---|---|
| `id` | string | MongoDB ObjectId | Luôn |
| `productID` | int | Numeric ID | Luôn |
| `name` | string | Tên sản phẩm đầy đủ | Luôn |
| `searchName` | string | Tên không dấu | Luôn |
| `slug` | string | URL slug | Luôn |
| `sku` | string | Mã SKU | Luôn |
| `basePrice` | float | **Giá sỉ** | **Có auth** |
| `pricingTablePrice` | float | Giá theo bảng giá | **Có auth** |
| `currentPrice` | float | Giá hiện tại (thường = 0) | Luôn |
| `manufacturer` | object | `{code, name}` | Luôn |
| `categories` | array | Danh mục | Luôn |
| `retailUnit` | string | Đơn vị (Hộp, Lọ, Tuýp) | Luôn |
| `stockStatus` | int | 1 = còn hàng | Luôn |
| `imageUrls` | array | URL ảnh sản phẩm | Luôn |
| `ingredients` | array | Thành phần | Luôn |
| `productInfo` | array | Thông tin chi tiết | Luôn |
| `quantityAvailable` | int | Số lượng tồn | Luôn |
| `updatedDate` | string | Ngày cập nhật | Luôn |

### 4.3. Mapping sang DrugPrice model

```python
DrugPrice(
    drug_name    = product["name"],
    brand        = product.get("manufacturer", {}).get("name", ""),
    manufacturer = product.get("manufacturer", {}).get("name", ""),
    dosage_form  = "",  # trong productInfo type=1
    strength     = "",  # trong ingredients
    price_vnd    = product.get("basePrice", 0),
    price_display = f"{product['basePrice']:,}đ",
    source       = SourceName.GIATHUOCTOT,
    source_url   = f"https://www.giathuoctot.com/product/{product['slug']}",
    crawled_at   = datetime.now(),
)
```

---

## 5. Categories

### 5.1. 6 Categories chính

```
GET https://api.giathuoctot.com/product/category
```

| # | Tên | CategoryId |
|---|---|---|
| 1 | Nhóm thuốc | `680fc4e5eb00213b6d497a6e` |
| 2 | Nhóm dược Mỹ Phẩm | — |
| 3 | Nhóm TPCN | `66a11336c435f94b3e07b6c9` |
| 4 | Nhóm TBYT | `66a11336c435f94b3e07b6ca` |
| 5 | Nhóm Hàng Tiêu Dùng | — |
| 6 | Nhóm SP độc quyền | `69cbb6268b778de53d8604c7` |

### 5.2. Product Group Categories (10 nhóm)

```
GET https://api.giathuoctot.com/configuration/configurationV2/retrieve-product-group-categories
```

| # | Tên | Sub-categories |
|---|---|---|
| 1 | Tất Cả Sản Phẩm | 0 |
| 2 | SP theo NSX | 16 |
| 3 | Thuốc kê đơn | 4 |
| 4 | Thuốc không kê đơn | 2 |
| 5 | Thực phẩm chức năng | 0 |
| 6 | Thiết bị y tế | 0 |
| 7 | Dược mỹ phẩm | 0 |
| 8 | Nhóm hàng tiêu dùng | 0 |
| 9 | SP độc quyền | 0 |
| 10 | SP lợi nhuận cao | 0 |

### 5.3. Filter theo category

```json
{
  "limit": 200,
  "offset": 0,
  "categoryId": "680fc4e5eb00213b6d497a6e"
}
```

---

## 6. Crawl Strategy

### 6.1. Crawl ALL (mặc định)

```python
# Pseudocode
total = 8250
limit = 200
all_products = []

for offset in range(0, total, limit):
    response = POST retrieve-products-client
    body = {"limit": limit, "offset": offset}

    products = response["products"]
    all_products.extend(products)

    sleep(2)  # rate limit

# Result: 8,250 products in 42 requests (~1.5 minutes)
```

### 6.2. Crawl với whitelist (sau này)

```python
# Whitelist theo category
body = {
    "limit": 200,
    "offset": 0,
    "categoryId": "680fc4e5eb00213b6d497a6e"  # chỉ thuốc kê đơn
}

# Whitelist theo keyword
body = {
    "limit": 200,
    "offset": 0,
    "searchTerm": "boganic"
}

# Whitelist theo manufacturer
# → Lọc client-side sau khi retrieve all
#    (API không hỗ trợ filter manufacturer trực tiếp)
```

### 6.3. Cache strategy

```
Cache key:   giathuoctot:all_products
Cache TTL:   24 giờ
Cache value: list[DrugPrice] + crawled_at

Flow:
  1. Check cache → hit & not expired → return cached
  2. Cache miss/expired → crawl 42 requests
  3. Save to cache
  4. Return data
```

### 6.4. Token management

```
Cache key:   giathuoctot:auth_token
Cache TTL:   24 giờ (token sống 25h, refresh trước 1h)

Flow:
  1. Check cached token
  2. Token valid → use it
  3. Token expired → refresh-token
     ├── Refresh OK → cache new token
     └── Refresh fail → re-login → cache new token
  4. API returns 401 → re-login → retry (max 3 lần)
```

### 6.5. Rate limiting

| Setting | Giá trị |
|---|---|
| Delay giữa requests | 2 giây |
| Max retries | 3 |
| Retry backoff | 5s → 10s → 20s |
| User-Agent | Chrome desktop |
| Headers | `from: Web`, `source: FE` |

---

## 7. Headers chuẩn

```
Content-Type: application/json
Accept: application/json
Authorization: Bearer <jwtToken>
from: Web
source: FE
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36
```

---

## 8. Code ví dụ (Python + httpx)

```python
import httpx
import asyncio
from datetime import datetime

class GiathuoctotCrawler:
    BASE_URL = "https://api.giathuoctot.com"
    LOGIN_URL = f"{BASE_URL}/authentication/account/v2/login"
    REFRESH_URL = f"{BASE_URL}/authentication/account/refresh-token"
    PRODUCTS_URL = f"{BASE_URL}/product/product/retrieve-products-client"

    HEADERS = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "from": "Web",
        "source": "FE",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...",
    }

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self.token = None
        self.refresh_token = None
        self.client = httpx.AsyncClient(headers=self.HEADERS, timeout=30)

    async def login(self):
        resp = await self.client.post(self.LOGIN_URL, json={
            "userName": self.username,
            "password": self.password,
        })
        data = resp.json()["data"]
        self.token = data["jwtToken"]
        self.refresh_token = data["refreshToken"]

    async def ensure_auth(self):
        if not self.token:
            await self.login()

    async def get_all_products(self, limit=200, delay=2):
        await self.ensure_auth()

        headers = {**self.HEADERS, "Authorization": f"Bearer {self.token}"}
        all_products = []
        offset = 0

        while True:
            resp = await self.client.post(
                self.PRODUCTS_URL,
                json={"limit": limit, "offset": offset},
                headers=headers,
            )

            if resp.status_code == 401:
                await self.login()
                headers["Authorization"] = f"Bearer {self.token}"
                continue

            data = resp.json()
            products = data.get("products", [])
            total = data.get("total", 0)

            all_products.extend(products)
            print(f"Offset {offset}: got {len(products)} (total: {total})")

            offset += limit
            if offset >= total:
                break

            await asyncio.sleep(delay)

        return all_products
```

---

## 9. Lưu ý

> **Pagination**: Dùng `limit` + `offset`, KHÔNG dùng `page` + `pageSize`. API ignore `page`/`pageSize` và luôn trả về 30 sản phẩm cố định nếu dùng sai param.

> **Giá**: Chỉ hiện khi dùng endpoint `-client` hoặc `-member` + JWT token. Endpoint `-guest` trả `basePrice = 0`.

> **reCAPTCHA**: Có siteKey trong config nhưng **không yêu cầu khi login API**. Có thể bật sau này → cần theo dõi.

> **Cloudflare**: Site dùng Cloudflare. Nếu bị block → tăng delay, dùng User-Agent thật. Không thấy anti-bot aggresssive ở thời điểm phân tích.

> **Image URLs**: Có 2 dạng:
> - `https://api.giathuoctot.com/filestorage/filestorage/get-image?token=...` (cần token)
> - `https://ecrms-appfiles.s3.ap-southeast-1.amazonaws.com/products/...` (public S3)

> **Rate limit**: Chưa thấy API trả 429. Nhưng nên giữ delay 2s để an toàn.

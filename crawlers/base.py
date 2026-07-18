"""BaseCrawler — bộ khung chung cho 9 crawler.

Ví von: như một chiếc xe tải nền — mỗi site chỉ cần lắp "thùng hàng" riêng
(cách login + cách đọc sản phẩm), còn động cơ (HTTP client, retry, rate limit,
tự đăng nhập lại khi rớt session) thì dùng chung.

Thiết kế auth: mỗi site có cách login quá khác nhau (JWT / session+CSRF /
OAuth PKCE / WordPress nonce) nên `_login()` là abstract để từng crawler tự
làm. Base lo phần *chung*: giữ trạng thái đã đăng nhập, và tự re-auth khi
request trả về lỗi xác thực (401 / redirect về trang login). Đây là phần
"AuthManager" trong PLAN.md, đặt ngay trong base cho gọn.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import Callable

import httpx

from utils.models import DrugPrice, SiteConfig, SourceName

LogFn = Callable[[str], None]


class CrawlError(Exception):
    """Lỗi crawl không thể phục hồi (sau khi đã retry)."""


class AuthError(CrawlError):
    """Đăng nhập/refresh token thất bại."""


class BaseCrawler(ABC):
    #: Bắt buộc override ở lớp con.
    source_name: SourceName
    #: False nếu API/site không lọc kết quả theo từ khóa phía server (vd bachhoathuoc
    #: trả nguyên catalog dù truyền keyword gì) — CrawlerEngine sẽ cache toàn bộ catalog
    #: (keyword rỗng) rồi tự lọc theo keyword thật, thay vì gọi lại API mỗi lần search.
    keyword_search_supported: bool = True

    def __init__(self, config: SiteConfig, log: LogFn | None = None):
        self.config = config
        self._log_fn = log or (lambda _msg: None)
        self._client: httpx.AsyncClient | None = None
        self._authenticated = False
        self._auth_time: float = 0.0
        self._token: str = ""  # Bearer token cho site kiểu API.

    # ----------------------------------------------------------------- helpers
    def log(self, message: str) -> None:
        self._log_fn(f"[{self.source_name.value}] {message}")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Crawler chưa mở. Dùng 'async with crawler:' hoặc gọi open().")
        return self._client

    async def open(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={
                    "User-Agent": self.config.user_agent,
                    "Accept": "application/json, text/html, */*",
                },
                timeout=30.0,
                follow_redirects=False,  # để tự phát hiện redirect-về-login
                http2=False,
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "BaseCrawler":
        await self.open()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    # ------------------------------------------------------------------- auth
    def _session_expired(self) -> bool:
        if not self._authenticated:
            return True
        age_h = (time.time() - self._auth_time) / 3600.0
        return age_h >= self.config.auth.expiry_hours

    async def ensure_auth(self) -> None:
        """Đăng nhập nếu chưa auth hoặc session đã quá hạn (theo expiry_hours)."""
        # Token dán tay (site khó login) → dùng luôn, bỏ qua login.
        if self.config.auth.manual_token and not self._authenticated:
            self._token = self.config.auth.manual_token
            self._authenticated = True
            self._auth_time = time.time()
            self.log("Dùng manual_token từ config.")
            return

        if self._session_expired():
            self.log("Đăng nhập...")
            await self._login()
            self._authenticated = True
            self._auth_time = time.time()
            self.log("Đăng nhập OK.")

    async def _reauth(self) -> None:
        self._authenticated = False
        await self.ensure_auth()

    # -------------------------------------------------------------- rate limit
    async def _throttle(self) -> None:
        await asyncio.sleep(self.config.rate_limit.delay_seconds)

    async def request_with_retry(
        self, method: str, url: str, *, allow_reauth: bool = True, **kwargs
    ) -> httpx.Response:
        """GET/POST có retry mạng (backoff) + tự re-auth khi gặp lỗi xác thực."""
        rl = self.config.rate_limit
        last_exc: Exception | None = None
        rate_limit_hits = 0

        for attempt in range(rl.max_retries):
            try:
                resp = await self.client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                backoff = rl.retry_backoff_seconds * (2 ** attempt)
                self.log(f"Lỗi mạng ({exc.__class__.__name__}), thử lại sau {backoff:.0f}s...")
                await asyncio.sleep(backoff)
                continue

            # 429 Too Many Requests — rate limited, chờ rồi retry (tối đa 2 lần).
            if resp.status_code == 429 and rate_limit_hits < 2:
                rate_limit_hits += 1
                backoff = 30 * (2 ** (rate_limit_hits - 1))
                self.log(f"Rate limited (429), thử lại sau {backoff:.0f}s...")
                await asyncio.sleep(backoff)
                continue

            # Phát hiện session hỏng → re-auth 1 lần rồi thử lại.
            if allow_reauth and self._is_auth_error(resp):
                if attempt < rl.max_retries - 1 and self.config.auth.retry_on_401:
                    self.log("Session hết hạn → đăng nhập lại...")
                    await self._reauth()
                    kwargs = self._inject_auth(kwargs)
                    continue
                raise AuthError(f"{self.source_name.value}: xác thực thất bại (HTTP {resp.status_code}).")

            return resp

        raise CrawlError(f"{self.source_name.value}: hết lượt retry. Lỗi cuối: {last_exc}")

    def _inject_auth(self, kwargs: dict) -> dict:
        """Cập nhật header Authorization sau khi re-auth (site kiểu Bearer).

        Site kiểu session-cookie không cần vì cookie jar tự cập nhật.
        """
        if self._token:
            headers = dict(kwargs.get("headers") or {})
            headers["Authorization"] = f"Bearer {self._token}"
            kwargs["headers"] = headers
        return kwargs

    def _is_auth_error(self, resp: httpx.Response) -> bool:
        """Mặc định: 401/403 là lỗi auth. Site cookie override để bắt redirect."""
        return resp.status_code in (401, 403)

    # ---------------------------------------------------------------- pipeline
    async def crawl(self, keyword: str) -> list[DrugPrice]:
        """Entry point: đảm bảo login → lấy raw → map sang DrugPrice."""
        await self.ensure_auth()
        raw_items = await self._fetch_products(keyword)
        results: list[DrugPrice] = []
        for raw in raw_items:
            try:
                price = self._parse_product(raw)
                if price is not None and price.drug_name:
                    results.append(price)
            except Exception as exc:  # 1 item hỏng không được làm hỏng cả mẻ
                self.log(f"Bỏ qua 1 sản phẩm lỗi parse: {exc}")
        self.log(f"Tìm thấy {len(results)} sản phẩm.")
        return results

    async def crawl_all(self) -> list[DrugPrice]:
        """"Lấy hết dữ liệu site này, có giá" — dùng cho catalog refresh (tên+id,
        giá bị bỏ khi lưu vào bảng catalog) và cho luồng CLI/batch export khi site
        không lọc được theo keyword (xem `keyword_search_supported`). Mặc định = một
        cú `crawl("")`; site cần chiến lược riêng (vd chia theo category để vượt
        cap phân trang) thì override.
        """
        return await self.crawl("")

    async def fetch_price_by_id(self, product_id: str) -> DrugPrice | None:
        """Lấy giá LIVE cho đúng 1 sản phẩm theo product_id — dùng khi user chọn
        1 kết quả cụ thể từ catalog và cần giá tại thời điểm đó, không qua
        `crawl_all()`/cache. Mặc định site không hỗ trợ; override nếu site có
        endpoint chi tiết theo id/SKU riêng.
        """
        raise NotImplementedError(
            f"{self.source_name.value}: không hỗ trợ fetch_price_by_id."
        )

    # ---------------------------------------------------------------- abstract
    @abstractmethod
    async def _login(self) -> None:
        """Đăng nhập, set cookie jar và/hoặc self._token."""

    @abstractmethod
    async def _fetch_products(self, keyword: str) -> list[dict]:
        """Gọi API/parse HTML, trả list raw dict."""

    @abstractmethod
    def _parse_product(self, raw: dict) -> DrugPrice | None:
        """Chuyển 1 raw item → DrugPrice (hoặc None nếu bỏ)."""

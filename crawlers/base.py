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
import threading
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


# Marker cố định trong message log khi `_login()` thất bại — GUI
# (gui/main_window.py `_drain_queue`) tìm chuỗi này để hiện popup cảnh báo,
# thay vì chỉ nằm im trong tab Log (dễ bị bỏ sót vì không phải tab đang xem).
AUTH_FAILURE_MARKER = "[!] ĐĂNG NHẬP THẤT BẠI"


# Cache phiên đăng nhập DÙNG CHUNG giữa các BaseCrawler instance khác nhau
# (kể cả khác thread) — keyed theo source_name. GUI tạo CrawlerEngine/crawler
# MỚI cho mỗi lần "Thêm" sản phẩm (mỗi lần trên 1 thread + event loop riêng,
# không thể chia sẻ trực tiếp 1 httpx client sống giữa các thread một cách an
# toàn), nên nếu không cache gì thì mỗi lần thêm đều phải đăng nhập THẬT lại
# dù vừa đăng nhập cho sản phẩm khác vài giây trước — gây spam "Đăng nhập..."
# và tốn thời gian không cần thiết.
#
# Ở đây chỉ cache ARTIFACT (token string + cookie dict, đều là dữ liệu bất
# biến/immutable, copy-safe giữa thread) chứ KHÔNG cache chính object client —
# an toàn hơn nhiều so với chia sẻ client/event-loop, và tự chữa lành: nếu
# artifact phục hồi bị sai/hết hạn phía server (không chỉ hết hạn theo thời
# gian), request đầu tiên sẽ trả 401 và cơ chế retry-401 có sẵn
# (`request_with_retry`/`_reauth`) sẽ tự đăng nhập thật lại như bình thường.
_AUTH_CACHE_LOCK = threading.Lock()
_AUTH_CACHE: dict[str, dict] = {}


def clear_auth_cache(source_name: SourceName | None = None) -> None:
    """Xoá cache phiên đăng nhập — gọi khi user đổi tài khoản (Sửa tài khoản)
    để tránh dùng nhầm phiên của tài khoản CŨ. `source_name=None` xoá hết."""
    with _AUTH_CACHE_LOCK:
        if source_name is None:
            _AUTH_CACHE.clear()
        else:
            _AUTH_CACHE.pop(source_name.value, None)


class BaseCrawler(ABC):
    #: Bắt buộc override ở lớp con.
    source_name: SourceName
    #: False nếu API/site không lọc kết quả theo từ khóa phía server (vd bachhoathuoc
    #: trả nguyên catalog dù truyền keyword gì) — CrawlerEngine sẽ cache toàn bộ catalog
    #: (keyword rỗng) rồi tự lọc theo keyword thật, thay vì gọi lại API mỗi lần search.
    keyword_search_supported: bool = True
    #: True khi site có endpoint chi tiết ổn định theo product_id/slug/SKU.
    #: Luồng GUI phải ưu tiên endpoint này thay vì tìm lại bằng tên hiển thị.
    direct_fetch_supported: bool = False

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

    async def ensure_auth(self, *, force_real_login: bool = False) -> None:
        """Đăng nhập nếu chưa auth hoặc session đã quá hạn (theo expiry_hours).

        Trước khi đăng nhập THẬT, thử phục hồi artifact (token/cookie) từ
        `_AUTH_CACHE` — nếu instance khác (crawl trước, có thể ở thread khác)
        vừa đăng nhập site này còn trong hạn thì dùng lại luôn, bỏ qua network
        round-trip. `force_real_login=True` (dùng khi `_reauth` — biết chắc
        phiên hiện tại đã hỏng) bỏ qua bước phục hồi, luôn đăng nhập thật."""
        # Token dán tay (site khó login) → dùng luôn, bỏ qua login.
        if self.config.auth.manual_token and not self._authenticated:
            self._token = self.config.auth.manual_token
            self._authenticated = True
            self._auth_time = time.time()
            self.log("Dùng manual_token từ config.")
            return

        if self._session_expired():
            if not force_real_login and self._restore_cached_auth():
                return
            self.log("Đăng nhập...")
            try:
                await self._login()
            except Exception as exc:
                # Gói lại thành AuthError đồng nhất cho cả 9 site (mỗi site
                # _login() có thể raise đủ loại exception khác nhau tuỳ cơ chế
                # auth) + gắn AUTH_FAILURE_MARKER vào log để GUI nhận diện và
                # popup cảnh báo, không chỉ nằm im trong tab Log.
                self.log(f"{AUTH_FAILURE_MARKER}: {exc}")
                raise AuthError(f"{self.source_name.value}: đăng nhập thất bại — {exc}") from exc
            self._authenticated = True
            self._auth_time = time.time()
            self._save_auth_cache()
            self.log("Đăng nhập OK.")

    async def _reauth(self) -> None:
        self._authenticated = False
        clear_auth_cache(self.source_name)
        await self.ensure_auth(force_real_login=True)

    def _restore_cached_auth(self) -> bool:
        cached = _AUTH_CACHE.get(self.source_name.value)
        if cached is None:
            return False
        age_h = (time.time() - cached["auth_time"]) / 3600.0
        if age_h >= self.config.auth.expiry_hours:
            return False
        self._token = cached.get("token", "")
        if cached.get("cookies") and self._client is not None:
            for c in cached["cookies"]:
                self._client.cookies.set(c["name"], c["value"], c.get("domain", ""), c.get("path", "/"))
        if cached.get("extra"):
            self._restore_extra_auth_state(cached["extra"])
        self._authenticated = True
        self._auth_time = cached["auth_time"]
        self.log(f"Dùng lại phiên đăng nhập trước ({age_h:.1f}h, còn hạn) — bỏ qua đăng nhập.")
        return True

    def _save_auth_cache(self) -> None:
        entry: dict = {"token": self._token, "auth_time": self._auth_time}
        if self._client is not None:
            # KHÔNG dùng dict(self._client.cookies): Cookies là MutableMapping nên
            # dict() tra qua __getitem__ -> get(name), ném CookieConflict nếu server
            # (vd WordPress ở duocphamgiasi) set 2+ cookie CÙNG TÊN khác domain/path
            # (wordpress_sec_* cho "/" và "/wp-admin"). Lưu đủ (name, value, domain,
            # path) từng cookie để không phụ thuộc tên duy nhất và không mất cookie nào.
            entry["cookies"] = [
                {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
                for c in self._client.cookies.jar
            ]
        extra = self._extra_auth_state()
        if extra:
            entry["extra"] = extra
        with _AUTH_CACHE_LOCK:
            _AUTH_CACHE[self.source_name.value] = entry

    def _extra_auth_state(self) -> dict:
        """Override ở lớp con nếu `_login()` còn set state khác ngoài
        self._token/cookie jar — vd chothuoc247 gắn CSRF token với session,
        thiếu 1 trong 2 là lỗi HTTP 419 dù cookie vẫn còn hạn. Mặc định rỗng
        (đa số site chỉ cần token hoặc cookie thuần)."""
        return {}

    def _restore_extra_auth_state(self, extra: dict) -> None:
        """Cặp với `_extra_auth_state` — phục hồi state đó vào instance mới."""

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

"""Tests cho crawlers.base.BaseCrawler via a concrete subclass + MockTransport."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from crawlers.base import AuthError, BaseCrawler, CrawlError, clear_auth_cache
from utils.models import DrugPrice, SiteConfig, SourceName


@pytest.fixture(autouse=True)
def _isolated_auth_cache():
    """`_AUTH_CACHE` (crawlers/base.py) là module-level, dùng chung giữa mọi
    instance để cho phép tái sử dụng phiên đăng nhập thật giữa các lần
    fetch (xem docstring `BaseCrawler.ensure_auth`) — nhưng vì vậy PHẢI dọn
    sạch giữa các test, không thì test sau kế thừa nhầm cache của test trước
    (cùng source_name=GIATHUOCTOT ở _FakeCrawler) và login_count sai lệch."""
    clear_auth_cache()
    yield
    clear_auth_cache()


async def _noop(*_args: object, **_kwargs: object) -> None:
    return None


def _make_config(
    *,
    manual_token: str = "",
    retry_on_401: bool = True,
    expiry_hours: int = 12,
    max_retries: int = 3,
    delay_seconds: float = 0.0,
) -> SiteConfig:
    cfg = SiteConfig(id="test")
    cfg.base_url = "https://example.test"
    cfg.credentials.username = "u"
    cfg.credentials.password = "p"
    cfg.auth.manual_token = manual_token
    cfg.auth.retry_on_401 = retry_on_401
    cfg.auth.expiry_hours = expiry_hours
    cfg.auth.max_auth_retries = max_retries
    cfg.rate_limit.delay_seconds = delay_seconds
    cfg.rate_limit.max_retries = max_retries
    cfg.rate_limit.retry_backoff_seconds = 0.0
    return cfg


class _FakeCrawler(BaseCrawler):
    source_name = SourceName.GIATHUOCTOT

    def __init__(self, config: SiteConfig, log=None) -> None:
        super().__init__(config, log)
        self.login_count = 0

    async def _login(self) -> None:
        self.login_count += 1
        self._token = "tok"

    async def _fetch_products(self, keyword: str) -> list[dict[str, Any]]:
        return [
            {"drug_name": "Valid", "price": 1000},
            {"drug_name": "", "price": 2000},  # filtered out (empty name)
            {"drug_name": "NoneParse"},  # _parse_product returns None for this
        ]

    def _parse_product(self, raw: dict[str, Any]) -> DrugPrice | None:
        if raw.get("drug_name") == "NoneParse":
            return None
        return DrugPrice(
            drug_name=raw.get("drug_name", ""),
            price_vnd=raw.get("price", 0),
            source=self.source_name,
        )


def _attach_mock(crawler: BaseCrawler, handler) -> None:
    """Replace crawler._client with one using MockTransport."""
    crawler._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )


class TestOpenClose:
    def test_open_creates_client(self) -> None:
        c = _FakeCrawler(_make_config())
        asyncio.run(c.open())
        assert c._client is not None
        asyncio.run(c.close())
        assert c._client is None

    def test_close_idempotent(self) -> None:
        c = _FakeCrawler(_make_config())
        asyncio.run(c.open())
        asyncio.run(c.close())
        asyncio.run(c.close())  # no error

    def test_client_raises_when_not_open(self) -> None:
        c = _FakeCrawler(_make_config())
        with pytest.raises(RuntimeError):
            _ = c.client

    def test_aenter_aexit(self) -> None:
        async def run() -> None:
            c = _FakeCrawler(_make_config())
            async with c:
                assert c._client is not None
            assert c._client is None

        asyncio.run(run())


class TestRequestWithRetry:
    def test_happy_path_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        c = _FakeCrawler(_make_config())

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        asyncio.run(c.open())
        _attach_mock(c, handler)
        resp = asyncio.run(c.request_with_retry("GET", "https://example.test/x"))
        assert resp.status_code == 200
        asyncio.run(c.close())

    def test_429_then_200(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        c = _FakeCrawler(_make_config())
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429)
            return httpx.Response(200, json={"ok": True})

        asyncio.run(c.open())
        _attach_mock(c, handler)
        resp = asyncio.run(c.request_with_retry("GET", "https://example.test/x"))
        assert resp.status_code == 200
        assert calls["n"] >= 2
        asyncio.run(c.close())

    def test_401_triggers_reauth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        c = _FakeCrawler(_make_config())

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        asyncio.run(c.open())
        _attach_mock(c, handler)
        with pytest.raises(AuthError):
            asyncio.run(c.request_with_retry("GET", "https://example.test/x"))
        # _login called during reauth attempt.
        assert c.login_count >= 1
        asyncio.run(c.close())

    def test_401_reauth_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        c = _FakeCrawler(_make_config(max_retries=3))
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(401)
            return httpx.Response(200, json={"ok": True})

        asyncio.run(c.open())
        _attach_mock(c, handler)
        resp = asyncio.run(c.request_with_retry("GET", "https://example.test/x"))
        assert resp.status_code == 200
        assert c.login_count >= 1
        asyncio.run(c.close())

    def test_network_error_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        c = _FakeCrawler(_make_config(max_retries=2))
        calls = {"n": 0}

        def handler(req: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, json={"ok": True})

        asyncio.run(c.open())
        _attach_mock(c, handler)
        resp = asyncio.run(c.request_with_retry("GET", "https://example.test/x"))
        assert resp.status_code == 200
        asyncio.run(c.close())

    def test_all_retries_exhausted_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        c = _FakeCrawler(_make_config(max_retries=2))

        def handler(req: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("always fail")

        asyncio.run(c.open())
        _attach_mock(c, handler)
        with pytest.raises(CrawlError):
            asyncio.run(c.request_with_retry("GET", "https://example.test/x"))
        asyncio.run(c.close())

    def test_no_reauth_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        c = _FakeCrawler(_make_config(retry_on_401=False))

        def handler(req: httpx.Request) -> httpx.Response:
            return httpx.Response(401)

        asyncio.run(c.open())
        _attach_mock(c, handler)
        with pytest.raises(AuthError):
            asyncio.run(c.request_with_retry("GET", "https://example.test/x"))
        asyncio.run(c.close())


class TestEnsureAuth:
    def test_uses_manual_token(self) -> None:
        c = _FakeCrawler(_make_config(manual_token="manualXYZ"))
        asyncio.run(c.open())
        asyncio.run(c.ensure_auth())
        assert c._token == "manualXYZ"
        assert c._authenticated is True
        assert c.login_count == 0
        asyncio.run(c.close())

    def test_calls_login_when_no_token(self) -> None:
        c = _FakeCrawler(_make_config())
        asyncio.run(c.open())
        asyncio.run(c.ensure_auth())
        assert c._token == "tok"
        assert c._authenticated is True
        assert c.login_count == 1
        asyncio.run(c.close())

    def test_does_not_relogin_within_expiry(self) -> None:
        c = _FakeCrawler(_make_config(expiry_hours=12))
        asyncio.run(c.open())
        asyncio.run(c.ensure_auth())
        asyncio.run(c.ensure_auth())  # second call, session fresh
        assert c.login_count == 1
        asyncio.run(c.close())

    def test_relogin_after_expiry(self) -> None:
        c = _FakeCrawler(_make_config(expiry_hours=12))
        asyncio.run(c.open())
        asyncio.run(c.ensure_auth())
        # Simulate old auth time. Cache dùng chung (crawlers/base.py) vẫn còn
        # "tươi" theo timestamp thật — phải dọn luôn cache thì mới ép được
        # relogin THẬT (không thì restore từ cache, xem
        # test_restores_from_cache_instead_of_relogin bên dưới).
        c._auth_time = time.time() - 13 * 3600
        clear_auth_cache(c.source_name)
        asyncio.run(c.ensure_auth())
        assert c.login_count == 2
        asyncio.run(c.close())

    def test_restores_from_cache_instead_of_relogin(self) -> None:
        """Instance khác (giả lập lần fetch trước, có thể ở thread khác) đã
        đăng nhập cho CÙNG site — instance MỚI phải tái dùng token/cookie đó
        thay vì gọi `_login()` thật lại (đây là tính năng chính của cache)."""
        c1 = _FakeCrawler(_make_config(expiry_hours=12))
        asyncio.run(c1.open())
        asyncio.run(c1.ensure_auth())
        asyncio.run(c1.close())

        c2 = _FakeCrawler(_make_config(expiry_hours=12))
        asyncio.run(c2.open())
        asyncio.run(c2.ensure_auth())
        assert c2.login_count == 0, "phải dùng lại cache, không được login thật lại"
        assert c2._token == "tok"
        assert c2._authenticated is True
        asyncio.run(c2.close())

    def test_cache_expired_by_time_forces_relogin(self) -> None:
        """Cache dùng chung cũng phải tôn trọng `expiry_hours` — không phải
        cứ có cache là dùng mãi mãi."""
        c1 = _FakeCrawler(_make_config(expiry_hours=12))
        asyncio.run(c1.open())
        asyncio.run(c1.ensure_auth())
        asyncio.run(c1.close())

        from crawlers import base as base_mod
        cached = base_mod._AUTH_CACHE[SourceName.GIATHUOCTOT.value]
        cached["auth_time"] = time.time() - 13 * 3600  # giả lập cache cũ

        c2 = _FakeCrawler(_make_config(expiry_hours=12))
        asyncio.run(c2.open())
        asyncio.run(c2.ensure_auth())
        assert c2.login_count == 1, "cache qua han thi phai login that"
        asyncio.run(c2.close())

    def test_reauth_bypasses_cache_and_relogins(self) -> None:
        """`_reauth()` (401 giữa chừng) biết chắc session hiện tại đã hỏng —
        phải bỏ qua cache (dù cache còn 'tươi' theo timestamp) và login THẬT
        lại, không thì có thể lặp vô ích với 1 cache đã bị site vô hiệu hoá."""
        c1 = _FakeCrawler(_make_config(expiry_hours=12))
        asyncio.run(c1.open())
        asyncio.run(c1.ensure_auth())
        asyncio.run(c1.close())

        c2 = _FakeCrawler(_make_config(expiry_hours=12))
        asyncio.run(c2.open())
        asyncio.run(c2._reauth())
        assert c2.login_count == 1, "_reauth phải login thật, không được dùng cache"
        asyncio.run(c2.close())


class TestEnsureAuthLoginFailure:
    """`_login()` thất bại (sai tài khoản, lỗi mạng lúc login...) phải luôn
    được gói lại thành `AuthError` + gắn `AUTH_FAILURE_MARKER` vào log — GUI
    (gui/main_window.py `_drain_queue`) dựa vào marker này để popup cảnh báo."""

    class _FailingLoginCrawler(_FakeCrawler):
        async def _login(self) -> None:
            self.login_count += 1
            raise ValueError("sai mat khau")

    def test_wraps_any_exception_as_autherror(self) -> None:
        c = self._FailingLoginCrawler(_make_config())
        asyncio.run(c.open())
        with pytest.raises(AuthError):
            asyncio.run(c.ensure_auth())
        asyncio.run(c.close())

    def test_logs_with_auth_failure_marker(self) -> None:
        from crawlers.base import AUTH_FAILURE_MARKER

        logged: list[str] = []
        c = self._FailingLoginCrawler(_make_config(), log=logged.append)
        asyncio.run(c.open())
        with pytest.raises(AuthError):
            asyncio.run(c.ensure_auth())
        asyncio.run(c.close())

        assert any(AUTH_FAILURE_MARKER in msg for msg in logged)
        assert any("sai mat khau" in msg for msg in logged)

    def test_failed_login_not_cached(self) -> None:
        """Login fail thì KHÔNG được lưu vào _AUTH_CACHE — không thì instance
        khác sau đó sẽ "phục hồi" nhầm 1 phiên chưa từng đăng nhập thành công."""
        from crawlers import base as base_mod

        c = self._FailingLoginCrawler(_make_config())
        asyncio.run(c.open())
        with pytest.raises(AuthError):
            asyncio.run(c.ensure_auth())
        asyncio.run(c.close())

        assert SourceName.GIATHUOCTOT.value not in base_mod._AUTH_CACHE


class TestAuthCacheHelpers:
    def test_clear_specific_source(self) -> None:
        from crawlers import base as base_mod

        base_mod._AUTH_CACHE[SourceName.GIATHUOCTOT.value] = {"token": "x", "auth_time": time.time()}
        base_mod._AUTH_CACHE[SourceName.THUOCSI.value] = {"token": "y", "auth_time": time.time()}
        clear_auth_cache(SourceName.GIATHUOCTOT)
        assert SourceName.GIATHUOCTOT.value not in base_mod._AUTH_CACHE
        assert SourceName.THUOCSI.value in base_mod._AUTH_CACHE

    def test_clear_all_sources(self) -> None:
        from crawlers import base as base_mod

        base_mod._AUTH_CACHE[SourceName.GIATHUOCTOT.value] = {"token": "x", "auth_time": time.time()}
        clear_auth_cache()
        assert base_mod._AUTH_CACHE == {}

    def test_extra_auth_state_roundtrip(self) -> None:
        """Hook `_extra_auth_state`/`_restore_extra_auth_state` cho site có
        state khác ngoài token/cookie (vd CSRF gắn với session — chothuoc247)."""

        class _ExtraStateCrawler(_FakeCrawler):
            source_name = SourceName.CHOTHUOC247

            def __init__(self, config, log=None):
                super().__init__(config, log)
                self._csrf = ""

            async def _login(self) -> None:
                await super()._login()
                self._csrf = "csrf-abc"

            def _extra_auth_state(self) -> dict:
                return {"csrf": self._csrf}

            def _restore_extra_auth_state(self, extra: dict) -> None:
                self._csrf = extra.get("csrf", "")

        c1 = _ExtraStateCrawler(_make_config(expiry_hours=12))
        asyncio.run(c1.open())
        asyncio.run(c1.ensure_auth())
        asyncio.run(c1.close())

        c2 = _ExtraStateCrawler(_make_config(expiry_hours=12))
        asyncio.run(c2.open())
        asyncio.run(c2.ensure_auth())
        assert c2.login_count == 0
        assert c2._csrf == "csrf-abc", "extra state (csrf) phải được phục hồi cùng token/cookie"
        asyncio.run(c2.close())


class _DuplicateCookieCrawler(_FakeCrawler):
    """WordPress (duocphamgiasi) đặt 2 cookie CÙNG TÊN khác path (vd
    wordpress_sec_xxx cho "/" và "/wp-admin") — mô phỏng lại tình huống đó."""

    async def _login(self) -> None:
        await super()._login()
        self._client.cookies.set("wordpress_sec_abc", "val-root", domain="example.test", path="/")
        self._client.cookies.set("wordpress_sec_abc", "val-admin", domain="example.test", path="/wp-admin")


class TestAuthCacheDuplicateNamedCookies:
    """Regression: dict(client.cookies) từng ném CookieConflict ("Multiple cookies
    exist with name=...") khi 2+ cookie trùng tên khác domain/path — xem
    `_save_auth_cache`/`_restore_cached_auth`."""

    def test_save_does_not_raise_and_keeps_both(self) -> None:
        c = _DuplicateCookieCrawler(_make_config(expiry_hours=12))
        asyncio.run(c.open())
        asyncio.run(c.ensure_auth())  # trước đây crash CookieConflict ở đây
        asyncio.run(c.close())

        from crawlers import base as base_mod
        cached = base_mod._AUTH_CACHE[SourceName.GIATHUOCTOT.value]
        values = {ck["value"] for ck in cached["cookies"] if ck["name"] == "wordpress_sec_abc"}
        assert values == {"val-root", "val-admin"}

    def test_restore_reapplies_both_cookies(self) -> None:
        c1 = _DuplicateCookieCrawler(_make_config(expiry_hours=12))
        asyncio.run(c1.open())
        asyncio.run(c1.ensure_auth())
        asyncio.run(c1.close())

        c2 = _DuplicateCookieCrawler(_make_config(expiry_hours=12))
        asyncio.run(c2.open())
        asyncio.run(c2.ensure_auth())
        assert c2.login_count == 0, "phải restore từ cache, không login lại"
        restored_values = {
            ck.value for ck in c2._client.cookies.jar if ck.name == "wordpress_sec_abc"
        }
        assert restored_values == {"val-root", "val-admin"}
        asyncio.run(c2.close())


class TestSessionExpired:
    def test_not_authenticated_is_expired(self) -> None:
        c = _FakeCrawler(_make_config())
        assert c._session_expired() is True

    def test_fresh_session_not_expired(self) -> None:
        c = _FakeCrawler(_make_config())
        c._authenticated = True
        c._auth_time = time.time()
        assert c._session_expired() is False

    def test_old_session_expired(self) -> None:
        c = _FakeCrawler(_make_config(expiry_hours=1))
        c._authenticated = True
        c._auth_time = time.time() - 2 * 3600
        assert c._session_expired() is True


class TestIsAuthError:
    def test_401_is_auth_error(self) -> None:
        c = _FakeCrawler(_make_config())
        resp = httpx.Response(401)
        assert c._is_auth_error(resp) is True

    def test_403_is_auth_error(self) -> None:
        c = _FakeCrawler(_make_config())
        resp = httpx.Response(403)
        assert c._is_auth_error(resp) is True

    def test_200_not_auth_error(self) -> None:
        c = _FakeCrawler(_make_config())
        resp = httpx.Response(200)
        assert c._is_auth_error(resp) is False

    def test_500_not_auth_error(self) -> None:
        c = _FakeCrawler(_make_config())
        resp = httpx.Response(500)
        assert c._is_auth_error(resp) is False


class TestCrawlPipeline:
    def test_filters_empty_and_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        c = _FakeCrawler(_make_config(manual_token="X"))
        asyncio.run(c.open())
        results = asyncio.run(c.crawl("kw"))
        # _fetch returns 3: "Valid", "" (empty name -> filtered), "NoneParse" (None).
        assert len(results) == 1
        assert results[0].drug_name == "Valid"
        asyncio.run(c.close())

    def test_log_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        logs: list[str] = []
        c = _FakeCrawler(_make_config(manual_token="X"), log=logs.append)
        asyncio.run(c.open())
        asyncio.run(c.crawl("kw"))
        assert any("Giathuoctot" in m for m in logs)
        asyncio.run(c.close())


class TestInjectAuth:
    def test_adds_bearer_when_token_set(self) -> None:
        c = _FakeCrawler(_make_config())
        c._token = "abc"
        out = c._inject_auth({"method": "GET"})
        assert out["headers"]["Authorization"] == "Bearer abc"

    def test_no_auth_when_no_token(self) -> None:
        c = _FakeCrawler(_make_config())
        c._token = ""
        out = c._inject_auth({"method": "GET"})
        assert "headers" not in out

    def test_preserves_existing_headers(self) -> None:
        c = _FakeCrawler(_make_config())
        c._token = "abc"
        out = c._inject_auth({"headers": {"X-Custom": "1"}})
        assert out["headers"]["X-Custom"] == "1"
        assert out["headers"]["Authorization"] == "Bearer abc"


class TestThrottle:
    def test_throttle_calls_sleep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        c = _FakeCrawler(_make_config(delay_seconds=0.0))
        asyncio.run(c.open())
        asyncio.run(c._throttle())  # should not block with delay=0
        asyncio.run(c.close())


class TestCrawlAllAndFetchPriceByIdDefaults:
    """Mặc định trên BaseCrawler dùng cho site không cần chiến lược riêng (đối lập
    với bachhoathuoc — xem tests/test_crawlers_b2b.py::TestBachHoaThuoc)."""

    def test_crawl_all_defaults_to_crawl_empty_keyword(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("crawlers.base.asyncio.sleep", _noop)
        c = _FakeCrawler(_make_config(manual_token="X"))
        asyncio.run(c.open())
        results = asyncio.run(c.crawl_all())
        assert len(results) == 1
        assert results[0].drug_name == "Valid"
        asyncio.run(c.close())

    def test_fetch_price_by_id_not_implemented_by_default(self) -> None:
        c = _FakeCrawler(_make_config())
        with pytest.raises(NotImplementedError):
            asyncio.run(c.fetch_price_by_id("some-id"))

"""Tests cho crawlers.base.BaseCrawler via a concrete subclass + MockTransport."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from crawlers.base import AuthError, BaseCrawler, CrawlError
from utils.models import DrugPrice, SiteConfig, SourceName


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
        # Simulate old auth time.
        c._auth_time = time.time() - 13 * 3600
        asyncio.run(c.ensure_auth())
        assert c.login_count == 2
        asyncio.run(c.close())


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

"""Tests cho utils.models."""

from __future__ import annotations

from datetime import datetime

from utils.models import (
    AuthConfig,
    CacheConfig,
    CatalogItem,
    CSV_HEADERS,
    Credentials,
    DrugPrice,
    RateLimitConfig,
    SiteConfig,
    SourceName,
    StockStatus,
    WatchlistConfig,
    WatchlistItem,
)


class TestSourceName:
    def test_enum_values_present(self) -> None:
        assert SourceName.GIATHUOCTOT.value == "Giathuoctot"
        assert SourceName.CHOTHUOC247.value == "ChoThuoc247"
        assert SourceName.THUOCHAPU.value == "ThuocHaPu"
        assert SourceName.CHOTHUOCTOT.value == "ChoThuocTot"
        assert SourceName.THUOCSI.value == "ThuocSi"
        assert SourceName.THUOCTOT3MIEN.value == "ThuocTot3Mien"
        assert SourceName.THUOCSISAIGON.value == "ThuocSiSaiGon"
        assert SourceName.DUOCPHAMGIASI.value == "DuocPhamGiaSi"
        assert SourceName.BACHHOATHUOC.value == "BachHoaThuoc"

    def test_is_str_enum(self) -> None:
        assert isinstance(SourceName.GIATHUOCTOT, str)

    def test_count(self) -> None:
        assert len(list(SourceName)) == 9


class TestDrugPrice:
    def test_minimal_construct(self) -> None:
        dp = DrugPrice(drug_name="Paracetamol", source=SourceName.GIATHUOCTOT)
        assert dp.drug_name == "Paracetamol"
        assert dp.canonical_name == ""
        assert dp.source == SourceName.GIATHUOCTOT
        assert dp.brand == ""
        assert dp.price_vnd == 0
        assert dp.price_display == ""
        assert dp.stock_status == StockStatus.UNKNOWN
        assert isinstance(dp.crawled_at, datetime)

    def test_none_fields_coerced_to_empty(self) -> None:
        dp = DrugPrice(
            drug_name="X",
            source=SourceName.THUOCSI,
            canonical_name=None,  # type: ignore[arg-type]
            brand=None,  # type: ignore[arg-type]
            manufacturer=None,  # type: ignore[arg-type]
            dosage_form=None,  # type: ignore[arg-type]
            strength=None,  # type: ignore[arg-type]
            price_display=None,  # type: ignore[arg-type]
            source_url=None,  # type: ignore[arg-type]
        )
        assert dp.canonical_name == ""
        assert dp.brand == ""
        assert dp.manufacturer == ""
        assert dp.dosage_form == ""
        assert dp.strength == ""
        assert dp.price_display == ""
        assert dp.source_url == ""

    def test_full_construct(self) -> None:
        dp = DrugPrice(
            drug_name="Boganic",
            brand="Traphaco",
            manufacturer="Traphaco",
            dosage_form="Viên nén",
            strength="50mg",
            price_vnd=25000,
            price_display="25.000đ",
            source=SourceName.CHOTHUOC247,
            source_url="https://example.com/p/1",
        )
        assert dp.price_vnd == 25000
        assert dp.drug_name == "Boganic"

    def test_price_vnd_int_type(self) -> None:
        dp = DrugPrice(drug_name="X", source=SourceName.THUOCSI, price_vnd=999)
        assert isinstance(dp.price_vnd, int)


class TestCSVHeaders:
    def test_content(self) -> None:
        assert CSV_HEADERS == [
            "drug_name",
            "canonical_name",
            "brand",
            "manufacturer",
            "dosage_form",
            "strength",
            "price_vnd",
            "price_display",
            "stock_status",
            "source",
            "source_url",
            "crawled_at",
        ]

    def test_length(self) -> None:
        assert len(CSV_HEADERS) == 12

    def test_canonical_after_drug_name(self) -> None:
        assert CSV_HEADERS.index("canonical_name") == CSV_HEADERS.index("drug_name") + 1


class TestConfigModels:
    def test_credentials_defaults(self) -> None:
        c = Credentials()
        assert c.username == ""
        assert c.password == ""

    def test_auth_config_defaults(self) -> None:
        a = AuthConfig()
        assert a.method == "form_login"
        assert a.session_key == "session_id"
        assert a.expiry_hours == 12
        assert a.retry_on_401 is True
        assert a.max_auth_retries == 3
        assert a.manual_token == ""

    def test_cache_config_defaults(self) -> None:
        c = CacheConfig()
        assert c.enabled is True
        assert c.ttl_hours == 24

    def test_rate_limit_config_defaults(self) -> None:
        r = RateLimitConfig()
        assert r.delay_seconds == 2.0
        assert r.max_retries == 3
        assert r.retry_backoff_seconds == 5.0

    def test_site_config_defaults(self) -> None:
        s = SiteConfig(id="test")
        assert s.id == "test"
        assert s.name == ""
        assert s.enabled is True
        assert s.base_url == ""
        assert isinstance(s.credentials, Credentials)
        assert isinstance(s.auth, AuthConfig)
        assert isinstance(s.cache, CacheConfig)
        assert isinstance(s.rate_limit, RateLimitConfig)
        assert "Chrome" in s.user_agent

    def test_site_config_user_agent_default(self) -> None:
        s = SiteConfig(id="x")
        assert "Mozilla/5.0" in s.user_agent


class TestDrugPriceProductId:
    def test_product_id_defaults_empty(self) -> None:
        dp = DrugPrice(drug_name="X", source=SourceName.GIATHUOCTOT)
        assert dp.product_id == ""

    def test_product_id_set(self) -> None:
        dp = DrugPrice(drug_name="X", source=SourceName.GIATHUOCTOT, product_id="slug-123")
        assert dp.product_id == "slug-123"

    def test_product_id_none_coerced(self) -> None:
        dp = DrugPrice(drug_name="X", source=SourceName.GIATHUOCTOT, product_id=None)  # type: ignore[arg-type]
        assert dp.product_id == ""

    def test_product_id_not_in_csv_headers(self) -> None:
        assert "product_id" not in CSV_HEADERS


class TestImageUrl:
    def test_drug_price_image_url_default(self) -> None:
        dp = DrugPrice(drug_name="X", source=SourceName.GIATHUOCTOT)
        assert dp.image_url == ""

    def test_drug_price_image_url_set(self) -> None:
        dp = DrugPrice(drug_name="X", source=SourceName.GIATHUOCTOT, image_url="https://img.test/p.jpg")
        assert dp.image_url == "https://img.test/p.jpg"

    def test_drug_price_image_url_none_coerced(self) -> None:
        dp = DrugPrice(drug_name="X", source=SourceName.GIATHUOCTOT, image_url=None)  # type: ignore[arg-type]
        assert dp.image_url == ""

    def test_image_url_not_in_csv_headers(self) -> None:
        assert "image_url" not in CSV_HEADERS

    def test_catalog_item_image_url_default(self) -> None:
        ci = CatalogItem(product_id="p1", drug_name="X", source=SourceName.GIATHUOCTOT)
        assert ci.image_url == ""

    def test_catalog_item_image_url_set(self) -> None:
        ci = CatalogItem(product_id="p1", drug_name="X", source=SourceName.GIATHUOCTOT, image_url="https://img.test/c.jpg")
        assert ci.image_url == "https://img.test/c.jpg"

    def test_watchlist_item_image_url_default(self) -> None:
        wi = WatchlistItem(site_id="s", product_id="p", source=SourceName.GIATHUOCTOT, drug_name="X")
        assert wi.image_url == ""

    def test_watchlist_item_image_url_set(self) -> None:
        wi = WatchlistItem(site_id="s", product_id="p", source=SourceName.GIATHUOCTOT, drug_name="X", image_url="https://img.test/w.jpg")
        assert wi.image_url == "https://img.test/w.jpg"


class TestCatalogItem:
    def test_minimal_construct(self) -> None:
        ci = CatalogItem(product_id="p1", drug_name="Paracetamol", source=SourceName.GIATHUOCTOT)
        assert ci.product_id == "p1"
        assert ci.drug_name == "Paracetamol"
        assert ci.search_name == ""
        assert ci.manufacturer == ""
        assert ci.source == SourceName.GIATHUOCTOT
        assert ci.source_url == ""
        assert ci.master_product_id == ""
        assert isinstance(ci.cached_at, datetime)

    def test_full_construct(self) -> None:
        ci = CatalogItem(
            product_id="slug-abc",
            drug_name="Boganic Forte",
            search_name="boganic forte",
            manufacturer="Traphaco",
            source=SourceName.CHOTHUOC247,
            source_url="https://example.com/p/1",
            master_product_id="MP000001",
        )
        assert ci.search_name == "boganic forte"
        assert ci.manufacturer == "Traphaco"
        assert ci.master_product_id == "MP000001"

    def test_none_fields_coerced(self) -> None:
        ci = CatalogItem(
            product_id="p",
            drug_name=None,  # type: ignore[arg-type]
            source=SourceName.GIATHUOCTOT,
            manufacturer=None,  # type: ignore[arg-type]
            source_url=None,  # type: ignore[arg-type]
            search_name=None,  # type: ignore[arg-type]
            master_product_id=None,  # type: ignore[arg-type]
        )
        assert ci.drug_name == ""
        assert ci.manufacturer == ""
        assert ci.source_url == ""
        assert ci.search_name == ""
        assert ci.master_product_id == ""


class TestWatchlistItem:
    def test_defaults(self) -> None:
        wi = WatchlistItem(
            site_id="giathuoctot",
            product_id="p1",
            source=SourceName.GIATHUOCTOT,
            drug_name="X",
        )
        assert wi.site_id == "giathuoctot"
        assert wi.search_name == ""
        assert wi.added_at == 0.0
        assert wi.last_price_vnd == 0
        assert wi.last_checked == 0.0

    def test_full_construct(self) -> None:
        wi = WatchlistItem(
            site_id="chothuoc247",
            product_id="42",
            source=SourceName.CHOTHUOC247,
            drug_name="Boganic",
            search_name="boganic",
            added_at=1700000000.0,
            last_price_vnd=67000,
            last_checked=1700000100.0,
        )
        assert wi.last_price_vnd == 67000
        assert wi.search_name == "boganic"

    def test_none_fields_coerced(self) -> None:
        wi = WatchlistItem(
            site_id="s",
            product_id="p",
            source=SourceName.GIATHUOCTOT,
            drug_name=None,  # type: ignore[arg-type]
            search_name=None,  # type: ignore[arg-type]
        )
        assert wi.drug_name == ""
        assert wi.search_name == ""


class TestWatchlistConfig:
    def test_defaults(self) -> None:
        wc = WatchlistConfig()
        assert wc.refresh_interval_minutes == 10

    def test_custom_values(self) -> None:
        wc = WatchlistConfig(refresh_interval_minutes=5)
        assert wc.refresh_interval_minutes == 5

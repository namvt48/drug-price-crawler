"""Tests cho utils.price_parser."""

from __future__ import annotations

from utils.price_parser import format_price, parse_price


class TestParsePrice:
    def test_string_vnd_with_dong_suffix(self) -> None:
        assert parse_price("48.000đ") == 48000

    def test_float_value(self) -> None:
        assert parse_price(47100.0) == 47100

    def test_none_returns_zero(self) -> None:
        assert parse_price(None) == 0

    def test_empty_string_returns_zero(self) -> None:
        assert parse_price("") == 0

    def test_int_passthrough(self) -> None:
        assert parse_price(12345) == 12345

    def test_thousands_separators_commas(self) -> None:
        assert parse_price("1,234,567") == 1234567

    def test_pure_letters_returns_zero(self) -> None:
        assert parse_price("abc") == 0

    def test_whitespace_only_returns_zero(self) -> None:
        assert parse_price("   ") == 0

    def test_mixed_text_with_digits(self) -> None:
        assert parse_price("Giá: 25.500 VND") == 25500

    def test_zero_int(self) -> None:
        assert parse_price(0) == 0

    def test_float_zero(self) -> None:
        assert parse_price(0.0) == 0

    def test_negative_int_truncates(self) -> None:
        # int(-12.7) -> -12; behavior matches doc "cắt phần thập phân".
        assert parse_price(-12.7) == -12


class TestFormatPrice:
    def test_typical_value(self) -> None:
        assert format_price(25000) == "25.000đ"

    def test_zero(self) -> None:
        assert format_price(0) == "0đ"

    def test_large_value(self) -> None:
        assert format_price(1234567) == "1.234.567đ"

    def test_one_thousand(self) -> None:
        assert format_price(1000) == "1.000đ"

    def test_negative_zero_falsy(self) -> None:
        # `not value` is True for 0 only; -0 also falsy.
        assert format_price(-0) == "0đ"

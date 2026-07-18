"""Tests cho utils.csv_writer."""

from __future__ import annotations

import csv
from pathlib import Path

from utils.csv_writer import CsvWriter, _key
from utils.models import DrugPrice, SourceName


def _make(name: str, source: SourceName, price: int = 1000) -> DrugPrice:
    return DrugPrice(
        drug_name=name,
        price_vnd=price,
        price_display=f"{price}đ",
        source=source,
        source_url="https://example.com",
    )


def _read(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class TestCsvWriter:
    def test_write_fresh(self, tmp_path: Path) -> None:
        f = tmp_path / "out.csv"
        writer = CsvWriter(f)
        n = writer.write([_make("A", SourceName.GIATHUOCTOT), _make("B", SourceName.THUOCSI)])
        assert f.exists()
        assert n == 2
        rows = _read(f)
        assert len(rows) == 2
        assert rows[0]["drug_name"] == "A"
        assert rows[0]["source"] == "Giathuoctot"
        assert rows[0]["price_vnd"] == "1000"

    def test_header_present(self, tmp_path: Path) -> None:
        f = tmp_path / "out.csv"
        CsvWriter(f).write([_make("A", SourceName.GIATHUOCTOT)])
        with f.open("r", encoding="utf-8-sig", newline="") as fh:
            header = fh.readline().strip()
        assert "drug_name" in header
        assert "canonical_name" in header
        assert "source" in header
        assert "crawled_at" in header

    def test_utf8_sig_bom(self, tmp_path: Path) -> None:
        f = tmp_path / "out.csv"
        CsvWriter(f).write([_make("Vietnamese: Boganic", SourceName.GIATHUOCTOT)])
        raw = f.read_bytes()
        assert raw[:3] == b"\xef\xbb\xbf"  # BOM

    def test_dedup_overwrite_same_key(self, tmp_path: Path) -> None:
        f = tmp_path / "out.csv"
        w = CsvWriter(f)
        w.write([_make("A", SourceName.GIATHUOCTOT, price=1000)])
        # Same (name, source) -> overwrite price.
        n = w.write([_make("A", SourceName.GIATHUOCTOT, price=2500)])
        assert n == 1
        rows = _read(f)
        assert len(rows) == 1
        assert rows[0]["price_vnd"] == "2500"

    def test_new_key_appended(self, tmp_path: Path) -> None:
        f = tmp_path / "out.csv"
        w = CsvWriter(f)
        w.write([_make("A", SourceName.GIATHUOCTOT, price=1000)])
        n = w.write([_make("B", SourceName.THUOCSI, price=2000)])
        assert n == 2
        rows = _read(f)
        names = sorted(r["drug_name"] for r in rows)
        assert names == ["A", "B"]

    def test_dedup_case_insensitive(self, tmp_path: Path) -> None:
        f = tmp_path / "out.csv"
        w = CsvWriter(f)
        w.write([_make("Paracetamol", SourceName.GIATHUOCTOT)])
        n = w.write([_make("PARACETAMOL", SourceName.GIATHUOCTOT, price=5000)])
        assert n == 1
        rows = _read(f)
        assert len(rows) == 1

    def test_to_row_source_enum(self) -> None:
        dp = _make("X", SourceName.BACHHOATHUOC)
        row = CsvWriter._to_row(dp)
        assert row["source"] == "BachHoaThuoc"
        assert row["drug_name"] == "X"
        assert row["price_vnd"] == "1000"

    def test_to_row_all_fields(self) -> None:
        dp = DrugPrice(
            drug_name="D",
            canonical_name="D Canonical",
            brand="B",
            manufacturer="M",
            dosage_form="Tab",
            strength="50mg",
            price_vnd=999,
            price_display="999đ",
            source=SourceName.CHOTHUOC247,
            source_url="https://x.com",
        )
        row = CsvWriter._to_row(dp)
        assert row["canonical_name"] == "D Canonical"
        assert row["brand"] == "B"
        assert row["manufacturer"] == "M"
        assert row["dosage_form"] == "Tab"
        assert row["strength"] == "50mg"
        assert "T" in row["crawled_at"]  # ISO datetime

    def test_to_row_canonical_name_default(self) -> None:
        dp = _make("X", SourceName.GIATHUOCTOT)
        row = CsvWriter._to_row(dp)
        assert row["canonical_name"] == ""

    def test_canonical_name_round_trip(self, tmp_path: Path) -> None:
        f = tmp_path / "out.csv"
        dp = DrugPrice(
            drug_name="Boganic siro Traphaco",
            canonical_name="Boganic Siro Traphaco",
            price_vnd=25000,
            source=SourceName.GIATHUOCTOT,
        )
        CsvWriter(f).write([dp])
        rows = _read(f)
        assert len(rows) == 1
        assert rows[0]["canonical_name"] == "Boganic Siro Traphaco"

    def test_key_function(self) -> None:
        assert _key("A", "Giathuoctot") == "a||giathuoctot"
        assert _key("  A  ", "  Giathuoctot  ") == "a||giathuoctot"

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        f = tmp_path / "sub" / "deep" / "out.csv"
        CsvWriter(f).write([_make("A", SourceName.GIATHUOCTOT)])
        assert f.exists()

    def test_empty_list_keeps_existing(self, tmp_path: Path) -> None:
        f = tmp_path / "out.csv"
        w = CsvWriter(f)
        w.write([_make("A", SourceName.GIATHUOCTOT)])
        n = w.write([])
        assert n == 1
        rows = _read(f)
        assert len(rows) == 1

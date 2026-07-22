"""Ghi/append DrugPrice vào CSV, dedup theo (drug_name + source).

Ví von: như cập nhật bảng giá treo tường — cùng một thuốc từ cùng một nguồn
thì ghi đè giá mới nhất, thuốc mới thì thêm dòng. Dùng utf-8-sig để Excel
mở tiếng Việt không bị lỗi font.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import CSV_HEADERS, DrugPrice


def _key(drug_name: str, source: str) -> str:
    return f"{drug_name.strip().lower()}||{source.strip().lower()}"


class CsvWriter:
    """Merge bản ghi mới vào CSV hiện có, ghi đè theo key, giữ dòng cũ khác key."""

    def __init__(self, filepath: str | Path):
        self.filepath = Path(filepath)

    def write(self, prices: list[DrugPrice]) -> int:
        """Trả về tổng số dòng sau khi ghi."""
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        rows: dict[str, dict[str, str]] = {}

        # 1. Nạp dữ liệu cũ (nếu file đã tồn tại).
        if self.filepath.exists():
            with self.filepath.open("r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    rows[_key(row.get("drug_name", ""), row.get("source", ""))] = row

        # 2. Ghi đè / thêm bản ghi mới.
        for price in prices:
            row = self._to_row(price)
            rows[_key(row["drug_name"], row["source"])] = row

        # 3. Ghi lại toàn bộ.
        with self.filepath.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_HEADERS)
            writer.writeheader()
            writer.writerows(rows.values())

        return len(rows)

    @staticmethod
    def _to_row(price: DrugPrice) -> dict[str, str]:
        return {
            "drug_name": price.drug_name,
            "canonical_name": price.canonical_name,
            "brand": price.brand,
            "manufacturer": price.manufacturer,
            "dosage_form": price.dosage_form,
            "strength": price.strength,
            "price_vnd": str(price.price_vnd),
            "price_display": price.price_display,
            "stock_status": price.stock_status.value,
            "source": price.source.value if hasattr(price.source, "value") else str(price.source),
            "source_url": price.source_url,
            "crawled_at": price.crawled_at.isoformat(timespec="seconds"),
        }

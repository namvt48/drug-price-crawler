"""Tests cho utils.selected_store — lưu/khôi phục danh sách 'Đã chọn' của GUI."""

from __future__ import annotations

from pathlib import Path

from utils.models import CatalogItem, DrugPrice, SourceName
from utils.selected_store import load_selected, save_selected


def _dp(name: str = "Boganic", source: SourceName = SourceName.GIATHUOCTOT, price: int = 1000) -> DrugPrice:
    return DrugPrice(drug_name=name, price_vnd=price, source=source)


def _ci(
    name: str = "Boganic",
    product_id: str = "p1",
    source: SourceName = SourceName.GIATHUOCTOT,
    master_product_id: str = "MP1",
) -> CatalogItem:
    return CatalogItem(
        product_id=product_id, drug_name=name, source=source, master_product_id=master_product_id
    )


class TestSaveLoadRoundtrip:
    def test_roundtrip_records_and_items(self, tmp_path: Path) -> None:
        path = tmp_path / "selected.json"
        selected = {"Boganic Chuẩn": [_dp(price=67000), _dp(source=SourceName.CHOTHUOC247, price=65000)]}
        catalog_items = {
            "Boganic Chuẩn": [
                _ci(product_id="p1", source=SourceName.GIATHUOCTOT),
                _ci(product_id="p2", source=SourceName.CHOTHUOC247),
            ]
        }
        save_selected(selected, catalog_items, path=path)
        loaded_selected, loaded_items = load_selected(path)

        assert set(loaded_selected) == {"Boganic Chuẩn"}
        assert [r.price_vnd for r in loaded_selected["Boganic Chuẩn"]] == [67000, 65000]
        assert {i.product_id for i in loaded_items["Boganic Chuẩn"]} == {"p1", "p2"}
        assert all(i.master_product_id == "MP1" for i in loaded_items["Boganic Chuẩn"])

    def test_multiple_groups(self, tmp_path: Path) -> None:
        path = tmp_path / "selected.json"
        selected = {"A": [_dp("A")], "B": [_dp("B")]}
        catalog_items = {"A": [_ci("A")], "B": [_ci("B", product_id="p2")]}
        save_selected(selected, catalog_items, path=path)
        loaded_selected, loaded_items = load_selected(path)
        assert set(loaded_selected) == {"A", "B"}
        assert set(loaded_items) == {"A", "B"}

    def test_empty_group_saved_and_restored(self, tmp_path: Path) -> None:
        """Sản phẩm không site nào trả giá (records=[]) vẫn phải lưu/khôi phục
        được — không phải lỗi, chỉ là chưa có giá."""
        path = tmp_path / "selected.json"
        save_selected({"NoGia": []}, {"NoGia": [_ci("NoGia")]}, path=path)
        loaded_selected, loaded_items = load_selected(path)
        assert loaded_selected == {"NoGia": []}
        assert len(loaded_items["NoGia"]) == 1

    def test_overwrites_previous_content(self, tmp_path: Path) -> None:
        path = tmp_path / "selected.json"
        save_selected({"A": [_dp("A")]}, {"A": [_ci("A")]}, path=path)
        save_selected({"B": [_dp("B")]}, {"B": [_ci("B", product_id="p2")]}, path=path)
        loaded_selected, _ = load_selected(path)
        assert set(loaded_selected) == {"B"}


class TestLoadEdgeCases:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        selected, items = load_selected(tmp_path / "nope.json")
        assert selected == {}
        assert items == {}

    def test_corrupted_json_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "selected.json"
        path.write_text("{not valid json", encoding="utf-8")
        selected, items = load_selected(path)
        assert selected == {}
        assert items == {}

    def test_malformed_entry_skipped_not_crash(self, tmp_path: Path) -> None:
        path = tmp_path / "selected.json"
        path.write_text('{"Bad": "not-a-dict", "Good": {"records": [], "items": []}}', encoding="utf-8")
        selected, items = load_selected(path)
        assert "Bad" not in selected
        assert "Good" in selected

    def test_default_path_used_when_none_given(self, tmp_path: Path, monkeypatch) -> None:
        import utils.selected_store as store_mod

        monkeypatch.setattr(store_mod, "app_base_dir", lambda: tmp_path)
        save_selected({"A": [_dp("A")]}, {"A": [_ci("A")]})
        assert (tmp_path / "output" / "selected_products.json").exists()
        selected, _ = load_selected()
        assert "A" in selected

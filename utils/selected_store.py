"""Lưu/khôi phục danh sách "Đã chọn" của GUI (gui/main_window.py) ra file JSON.

Ví von: như "giỏ hàng" được ghi nhớ lại — tắt app rồi mở lại thấy y nguyên,
không phải tìm/thêm lại từ đầu. Chỉ lưu CatalogItem (đủ để fetch giá lại) +
DrugPrice CUỐI CÙNG (để hiện ngay lúc mở app, không cần mạng) — KHÔNG tự động
fetch lại giá lúc khôi phục, tránh dội hàng loạt request vào các site cùng lúc
(rate-limit) — xem `gui.main_window.MainWindow._restore_selected`.
"""

from __future__ import annotations

import json
from pathlib import Path

from utils.config_loader import app_base_dir
from utils.models import CatalogItem, DrugPrice


def _default_path() -> Path:
    return app_base_dir() / "output" / "selected_products.json"


def save_selected(
    selected: dict[str, list[DrugPrice]],
    catalog_items: dict[str, list[CatalogItem]],
    path: str | Path | None = None,
) -> None:
    """Ghi toàn bộ danh sách đã chọn hiện tại ra file, ghi đè hoàn toàn (không
    merge) — gọi lại sau MỖI lần thêm/xóa để file luôn khớp trạng thái GUI.
    Ghi qua file tạm rồi rename để không hỏng file cũ nếu app bị tắt giữa chừng
    lúc đang ghi."""
    names = set(selected) | set(catalog_items)
    data = {
        name: {
            "records": [r.model_dump(mode="json") for r in selected.get(name, [])],
            "items": [i.model_dump(mode="json") for i in catalog_items.get(name, [])],
        }
        for name in names
    }
    out_path = Path(path) if path else _default_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(out_path)


def load_selected(
    path: str | Path | None = None,
) -> tuple[dict[str, list[DrugPrice]], dict[str, list[CatalogItem]]]:
    """Đọc lại danh sách đã lưu. File thiếu/hỏng → trả rỗng (không crash app)."""
    in_path = Path(path) if path else _default_path()
    if not in_path.exists():
        return {}, {}
    try:
        raw = json.loads(in_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}, {}

    selected: dict[str, list[DrugPrice]] = {}
    catalog_items: dict[str, list[CatalogItem]] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        try:
            selected[name] = [DrugPrice(**r) for r in entry.get("records", [])]
            catalog_items[name] = [CatalogItem(**i) for i in entry.get("items", [])]
        except (TypeError, ValueError):
            continue
    return selected, catalog_items

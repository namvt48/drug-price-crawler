"""ViewModel — logic thuần của GUI, không đụng tkinter.

Ví von: tách "đầu bếp" (tính toán: gom nhóm, tìm giá rẻ nhất, dựng chuỗi giá)
khỏi "người bưng bê" (main_window chỉ hiển thị). Nhờ vậy phần tính toán
test được bằng pytest mà không cần màn hình.
"""

from __future__ import annotations

from utils.models import CatalogItem, DrugPrice, WatchlistItem
from utils.normalizer import canonical_for, canonical_key, group_names, load_aliases


def build_groups(names: list[str], aliases: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Gom tên biến thể trong cache về {canonical_display: [tên gốc...]}.

    Alias tay (name_aliases.yaml) thắng kết quả gom tự động: biến thể có alias
    được chuyển sang nhóm canonical của alias.
    """
    if aliases is None:
        aliases = load_aliases()
    groups = group_names(names) if names else {}

    if not aliases:
        return groups

    moved: dict[str, list[str]] = {}
    for canon, variants in groups.items():
        for v in variants:
            target = aliases.get(v.strip().lower(), canon)
            moved.setdefault(target, []).append(v)
    return moved


def build_catalog_groups(
    items: list[CatalogItem], aliases: dict[str, str] | None = None
) -> dict[str, list[CatalogItem]]:
    """Như `build_groups` nhưng giữ nguyên `CatalogItem` (cần source+product_id để
    live-fetch giá ngay khi user chọn — xem `engine.fetch_live_prices`), không chỉ
    tên string.
    """
    if aliases is None:
        aliases = load_aliases()

    name_to_items: dict[str, list[CatalogItem]] = {}
    for it in items:
        name_to_items.setdefault(it.drug_name, []).append(it)

    name_groups = group_names(list(name_to_items.keys())) if name_to_items else {}

    if not aliases:
        return {
            canon: [it for v in variants for it in name_to_items[v]]
            for canon, variants in name_groups.items()
        }

    moved: dict[str, list[CatalogItem]] = {}
    for canon, variants in name_groups.items():
        for v in variants:
            target = aliases.get(v.strip().lower(), canon)
            moved.setdefault(target, []).extend(name_to_items[v])
    return moved


def suggest(groups: dict[str, list[str]], query: str, limit: int = 30) -> list[str]:
    """Danh sách tên canonical khớp query (không phân biệt hoa thường)."""
    q = query.strip().lower()
    out: list[str] = []
    for canon in groups:
        if not q or q in canon.lower():
            out.append(canon)
        if len(out) >= limit:
            break
    return out


def cheapest(records: list[DrugPrice]) -> DrugPrice | None:
    """Bản ghi giá thấp nhất (bỏ qua giá 0 = giá ẩn/chưa đăng nhập)."""
    priced = [p for p in records if p.price_vnd > 0]
    if not priced:
        return None
    return min(priced, key=lambda p: p.price_vnd)


def _source_of(p: DrugPrice) -> str:
    return p.source.value if hasattr(p.source, "value") else str(p.source)


def format_prices(records: list[DrugPrice]) -> str:
    """Chuỗi 'nguồn: giá; ...' — nguồn rẻ nhất được đánh dấu ★ ở đầu."""
    best = cheapest(records)
    parts: list[str] = []
    for p in records:
        src = _source_of(p)
        price = p.price_display or (f"{p.price_vnd:,}đ" if p.price_vnd else "")
        label = f"{src}: {price}" if price else src
        if best is not None and p is best:
            label = f"★{label}"
        parts.append(label)
    return "; ".join(parts)


def cheapest_label(records: list[DrugPrice]) -> str:
    """Nhãn cột 'Rẻ nhất': 'nguồn: giá' hoặc rỗng nếu mọi giá đều ẩn."""
    best = cheapest(records)
    if best is None:
        return ""
    price = best.price_display or f"{best.price_vnd:,}đ"
    return f"{_source_of(best)}: {price}"


def merge_selected(selected: dict[str, list[DrugPrice]]) -> list[DrugPrice]:
    """Gộp mọi bản ghi đã chọn thành một danh sách phẳng để export."""
    out: list[DrugPrice] = []
    for records in selected.values():
        out.extend(records)
    return out


def format_watchlist(items: list[WatchlistItem]) -> list[dict[str, str]]:
    """Format watchlist cho hiển thị: drug_name, source, price, last_checked, status."""
    from datetime import datetime

    out: list[dict[str, str]] = []
    for item in items:
        ts = (
            datetime.fromtimestamp(item.last_checked).strftime("%Y-%m-%d %H:%M")
            if item.last_checked
            else "—"
        )
        price_str = f"{item.last_price_vnd:,}đ" if item.last_price_vnd else "—"
        if item.last_checked == 0:
            status = "never"
        elif (datetime.now().timestamp() - item.last_checked) > 1800:
            status = "stale"
        else:
            status = "fresh"
        out.append({
            "drug_name": item.drug_name,
            "source": item.source.value if hasattr(item.source, "value") else str(item.source),
            "price": price_str,
            "last_checked": ts,
            "status": status,
            "image_url": item.image_url,
        })
    return out


def watchlist_summary(items: list[WatchlistItem]) -> str:
    """One-line summary: '12 mục | 8 đã có giá | 4 chưa check'."""
    total = len(items)
    priced = sum(1 for i in items if i.last_price_vnd > 0)
    unchecked = sum(1 for i in items if i.last_checked == 0)
    return f"{total} mục | {priced} đã có giá | {unchecked} chưa check"


def sort_watchlist(items: list[WatchlistItem]) -> list[WatchlistItem]:
    """Sort by drug_name, then by source."""
    return sorted(items, key=lambda i: (i.drug_name.lower(), i.source.value))


def search_seed_for(drug_name: str) -> str:
    """Từ khoá ngắn (brand token của canonical_key) để query catalog rộng quanh 1
    sản phẩm cụ thể — dùng khi tự tìm nhóm canonical từ 1 item (tính năng 'Thêm bằng
    URL'), thay vì user gõ tay như ô tìm kiếm thường."""
    return canonical_key(drug_name).split("|", 1)[0]


def resolve_group_for_item(
    item: CatalogItem,
    candidates: list[CatalogItem],
    aliases: dict[str, str] | None = None,
) -> tuple[str, list[CatalogItem]]:
    """Từ 1 CatalogItem tra được (vd theo URL) + danh sách candidate cùng brand
    (`engine.suggest_catalog(search_seed_for(item.drug_name))`) → nhóm canonical
    (tên gộp biến thể liên site) chứa đúng item đó, để sync giá cả nhóm qua các site
    khác. Fallback nhóm chỉ gồm item đó nếu nó bị rớt ngoài candidates (hiếm, do limit)."""
    groups = build_catalog_groups(candidates, aliases=aliases)
    for name, variants in groups.items():
        if any(v.source == item.source and v.product_id == item.product_id for v in variants):
            return name, variants
    return canonical_for(item.drug_name, aliases), [item]


def format_scan_summary(count: int, site_count: int, elapsed_seconds: float) -> str:
    """Message popup khi full scan (catalog cả 9 site) xong: số mục, số site, thời gian."""
    if elapsed_seconds >= 60:
        mins, secs = divmod(int(elapsed_seconds), 60)
        duration = f"{mins}m{secs:02d}s"
    else:
        duration = f"{elapsed_seconds:.0f}s"
    return f"Đã scan lại catalog toàn bộ {site_count} site — {count:,} mục — mất {duration}."

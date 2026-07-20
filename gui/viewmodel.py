"""ViewModel — logic thuần của GUI, không đụng tkinter.

Ví von: tách "đầu bếp" (tính toán: gom nhóm, tìm giá rẻ nhất, dựng chuỗi giá)
khỏi "người bưng bê" (main_window chỉ hiển thị). Nhờ vậy phần tính toán
test được bằng pytest mà không cần màn hình.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from utils.models import CatalogItem, DrugPrice, SourceName, WatchlistItem
from utils.normalizer import group_names, load_aliases

type SiteDescriptor = dict[str, str | SourceName]
type ProductDetailRow = dict[str, str]

T = TypeVar("T")


def _longest(names: list[str]) -> str:
    """Biến thể DÀI NHẤT trong 1 nhóm — dùng làm tên hiển thị. `group_names()`
    trả key là `display_name(canonical_key)` (đã strip dấu + nhiễu đóng gói để
    so khớp — vd 'Augmentin Gsk'), tốt cho MATCHING nhưng mất dấu tiếng Việt khi
    hiển thị cho người dùng. Tên gốc dài nhất trong nhóm thường là bản đầy đủ
    nhất (còn dấu + đóng gói), nên dùng để hiện lên UI thay vì cái key đó."""
    return max(names, key=len) if names else ""


def _relabel_by_longest_variant(
    groups: dict[str, list[T]], aliases: dict[str, str], name_of: Callable[[T], str]
) -> dict[str, list[T]]:
    """Đổi key hiển thị của mỗi nhóm: nhóm có alias tay (`name_aliases.yaml`)
    giữ nguyên tên đã cấu hình (chủ động, đáng tin) — nhóm tự động gộp thì đổi
    sang biến thể dài nhất (xem `_longest`) để không mất dấu tiếng Việt."""
    alias_targets: set[str] = set(aliases.values()) if aliases else set()
    return {
        (key if key in alias_targets else _longest([name_of(v) for v in variants])): variants
        for key, variants in groups.items()
    }


def build_groups(names: list[str], aliases: dict[str, str] | None = None) -> dict[str, list[str]]:
    """Gom tên biến thể trong cache về {tên hiển thị: [tên gốc...]}.

    Alias tay (name_aliases.yaml) thắng kết quả gom tự động: biến thể có alias
    được chuyển sang nhóm canonical của alias. Nhóm không có alias thì hiện tên
    là biến thể dài nhất trong nhóm (giữ dấu tiếng Việt), không phải khóa
    canonical đã bị strip dấu dùng để matching.
    """
    if aliases is None:
        aliases = load_aliases()
    groups = group_names(names) if names else {}

    if aliases:
        moved: dict[str, list[str]] = {}
        for canon, variants in groups.items():
            for v in variants:
                target = aliases.get(v.strip().lower(), canon)
                moved.setdefault(target, []).append(v)
        groups = moved

    return _relabel_by_longest_variant(groups, aliases, name_of=lambda v: v)


def build_catalog_groups(
    items: list[CatalogItem], aliases: dict[str, str] | None = None
) -> dict[str, list[CatalogItem]]:
    """Gom CatalogItem theo `master_product_id` — nhóm đã được entity-resolution
    (catalog_master_entity_resolved.xlsx) duyệt sẵn, KHÔNG fuzzy-match lại như
    `build_groups`. Mọi item cùng nhóm đã mang sẵn cùng 1 `drug_name` (tên chuẩn) từ
    `utils.catalog_master.load_master_catalog` nên chỉ cần group-by trực tiếp; alias
    tay (name_aliases.yaml) vẫn được áp lên trên để đổi tên hiển thị nếu cần.
    `master_product_id` rỗng (item dựng thủ công/test) → fallback nhóm theo
    `drug_name` để hàm vẫn hoạt động hợp lý.
    """
    if aliases is None:
        aliases = load_aliases()

    by_key: dict[str, list[CatalogItem]] = {}
    for it in items:
        key = it.master_product_id or it.drug_name
        by_key.setdefault(key, []).append(it)

    groups: dict[str, list[CatalogItem]] = {}
    for variants in by_key.values():
        canonical_name = variants[0].drug_name
        name = aliases.get(canonical_name.strip().lower(), canonical_name) if aliases else canonical_name
        groups.setdefault(name, []).extend(variants)
    return groups


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


def _site_status(
    site: SiteDescriptor,
    catalog_sources: set[SourceName],
    by_source: dict[SourceName, DrugPrice],
    best: DrugPrice | None,
) -> tuple[str, DrugPrice | None]:
    """Nhãn NGẮN (không kèm tên site) + record khớp (None nếu site không có
    giá) cho 1 site — logic trạng thái dùng chung giữa `price_cells_by_source`
    (chỉ cần nhãn ngắn cho 1 ô Treeview) và `product_detail_rows` (cần thêm
    nhà SX/thời gian/link nên cần cả record gốc, không chỉ nhãn). Trả lời câu
    'sao thuốc này chỉ có 2 nguồn, các site khác thì sao':
    - Site trả về giá live → hiển thị giá (★ = rẻ nhất).
    - Site có trong catalog (từng thấy sản phẩm này) nhưng live-fetch không ra
      giá → 'lỗi giá' (site đang lỗi/hết hàng tạm thời).
    - Site KHÔNG có trong catalog (nhóm sản phẩm này, theo entity-resolution,
      không có listing ở site đó) → 'không có SP' (site thật sự không bán thuốc
      này, hoặc catalog_master_entity_resolved.xlsx chưa cập nhật listing mới).
    """
    source_value = site["source"]
    source = source_value if isinstance(source_value, SourceName) else SourceName(source_value)
    rec = by_source.get(source)
    if rec is not None and rec.price_vnd > 0:
        price = rec.price_display or f"{rec.price_vnd:,}đ"
        label = f"★{price}" if best is not None and rec is best else price
        return label, rec
    if rec is not None:
        return "giá ẩn", rec
    if source in catalog_sources:
        return "lỗi giá", None
    return "không có SP", None


def price_cells_by_source(
    sites: list[SiteDescriptor],
    items: list[CatalogItem],
    records: list[DrugPrice],
) -> list[str]:
    """1 ô/site (đủ cả 9, không chỉ site có giá), theo đúng thứ tự `sites` — mỗi
    ô lên 1 cột Treeview riêng (tên site đã là header cột nên KHÔNG lặp lại tên
    trong nội dung ô). Xem `_site_status` cho ý nghĩa từng nhãn.

    `sites` là list dict {"name", "source"} theo thứ tự cố định (xem
    `main_window.MainWindow._site_order`/`_site_descriptors`).
    """
    catalog_sources = {it.source for it in items}
    by_source: dict[SourceName, DrugPrice] = {}
    for r in records:
        _ = by_source.setdefault(r.source, r)
    best = cheapest(records)
    return [_site_status(site, catalog_sources, by_source, best)[0] for site in sites]


def product_detail_rows(
    sites: list[SiteDescriptor],
    items: list[CatalogItem],
    records: list[DrugPrice],
) -> list[ProductDetailRow]:
    """1 dict/site cho bảng chi tiết sản phẩm (bấm chuột phải vào dòng 'Đã
    chọn') — như `price_cells_by_source` nhưng giàu thông tin hơn (nhà sản
    xuất, thời gian cập nhật, link sản phẩm) thay vì chỉ 1 nhãn ngắn cho cột
    Treeview. Field rỗng hiện '—'."""
    catalog_sources = {it.source for it in items}
    by_source: dict[SourceName, DrugPrice] = {}
    for r in records:
        _ = by_source.setdefault(r.source, r)
    best = cheapest(records)

    rows: list[ProductDetailRow] = []
    for site in sites:
        status, rec = _site_status(site, catalog_sources, by_source, best)
        if rec is not None:
            manufacturer = rec.manufacturer or "—"
            updated = rec.crawled_at.strftime("%H:%M %d/%m/%Y") if rec.crawled_at else "—"
            url = rec.source_url or "—"
        else:
            manufacturer = updated = url = "—"
        rows.append({
            "site": site["name"],
            "status": status,
            "manufacturer": manufacturer,
            "updated": updated,
            "url": url,
        })
    return rows


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

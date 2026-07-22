"""ViewModel — logic thuần của GUI, không đụng tkinter.

Ví von: tách "đầu bếp" (tính toán: gom nhóm, tìm giá rẻ nhất, dựng chuỗi giá)
khỏi "người bưng bê" (main_window chỉ hiển thị). Nhờ vậy phần tính toán
test được bằng pytest mà không cần màn hình.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from utils.models import CatalogItem, DrugPrice, SourceName, StockStatus, WatchlistItem
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
        (
            key if key in alias_targets else _longest([name_of(v) for v in variants])
        ): variants
        for key, variants in groups.items()
    }


def build_groups(
    names: list[str], aliases: dict[str, str] | None = None
) -> dict[str, list[str]]:
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
    (catalog_master.xlsx) duyệt sẵn, KHÔNG fuzzy-match lại như
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
        name = (
            aliases.get(canonical_name.strip().lower(), canonical_name)
            if aliases
            else canonical_name
        )
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
        if p.stock_status == StockStatus.OUT_OF_STOCK:
            label = f"{src}: hết hàng"
        else:
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
    - Site trả tín hiệu hết tồn kho rõ ràng → 'hết hàng'.
    - Site có trong catalog nhưng live-fetch không trả record → 'lỗi giá'.
    - Site KHÔNG có trong catalog (nhóm sản phẩm này, theo entity-resolution,
      không có listing ở site đó) → 'không có SP' (site thật sự không bán thuốc
      này, hoặc catalog_master.xlsx chưa cập nhật listing mới).
    """
    source_value = site["source"]
    source = (
        source_value
        if isinstance(source_value, SourceName)
        else SourceName(source_value)
    )
    rec = by_source.get(source)
    if rec is not None and rec.stock_status == StockStatus.OUT_OF_STOCK:
        return "hết hàng", rec
    if rec is not None and rec.price_vnd > 0:
        price = rec.price_display or f"{rec.price_vnd:,}đ"
        label = f"★{price}" if best is not None and rec is best else price
        return label, rec
    if rec is not None:
        return "giá ẩn", rec
    if source in catalog_sources:
        return "lỗi giá", None
    return "không có SP", None


def status_kind(label: str) -> str:
    """Đưa mọi nhãn giá/trạng thái về semantic key dùng chung cho màu UI.

    Hàm chấp nhận cả nhãn ngắn nội bộ (``lỗi giá``) lẫn nhãn đã trang trí
    (``! Lỗi giá``), để bảng chính, bảng chi tiết và màn kiểm tra sản phẩm mới
    không tự diễn giải trạng thái theo ba cách khác nhau.
    """
    normalized = label.strip().casefold()
    if "tốt nhất" in normalized or normalized.startswith("★"):
        return "best"
    if "hết hàng" in normalized:
        return "out"
    if "lỗi giá" in normalized:
        return "error"
    if "không có sp" in normalized:
        return "missing"
    if "giá ẩn" in normalized:
        return "hidden"
    if "chưa" in normalized or "đang" in normalized:
        return "pending"
    return "price"


def price_cell_display(label: str) -> str:
    """Nhãn rõ nghĩa cho ô giá ở bảng chính, không chỉ dựa vào màu/dấu sao."""
    kind = status_kind(label)
    if kind == "best":
        return f"★ Tốt nhất · {label.removeprefix('★')}"
    if kind == "price":
        return f"Giá · {label}"
    return {
        "out": "× Hết hàng",
        "error": "! Lỗi giá",
        "missing": "— Không có SP",
        "hidden": "! Giá ẩn",
        "pending": "… Chưa cập nhật",
    }[kind]


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
    return [
        price_cell_display(_site_status(site, catalog_sources, by_source, best)[0])
        for site in sites
    ]


def reconcile_records_with_items(
    items: list[CatalogItem], records: list[DrugPrice]
) -> tuple[list[DrugPrice], bool]:
    """Loại dữ liệu giá legacy không khớp chính xác catalog hiện tại.

    Các bản cũ từng giữ toàn bộ kết quả tìm theo tên nên một listing có thể kéo
    theo hàng chục thuốc gần giống. Chỉ giữ tối đa một record cho đúng cặp
    ``(source, product_id)`` đã lưu; trả thêm cờ để GUI tự crawl lại nhóm vừa
    được làm sạch đúng một lần.
    """
    record_by_identity = {
        (record.source, record.product_id): record
        for record in records
        if record.product_id
    }
    reconciled: list[DrugPrice] = []
    seen: set[tuple[SourceName, str]] = set()
    for item in items:
        identity = (item.source, item.product_id)
        if identity in seen:
            continue
        seen.add(identity)
        record = record_by_identity.get(identity)
        if record is not None:
            reconciled.append(record)
    return reconciled, len(reconciled) != len(records)


def product_detail_rows(
    sites: list[SiteDescriptor],
    items: list[CatalogItem],
    records: list[DrugPrice],
) -> list[ProductDetailRow]:
    """1 dict/site cho bảng chi tiết sản phẩm (chuột phải/nhấp đúp 'Đã chọn',
    hoặc 'Sửa' ở bảng tìm thuốc) — như `price_cells_by_source` nhưng tách
    riêng `price` (giá, "—" nếu chưa có) khỏi `status` ("Tốt" khi có giá thật,
    còn lại dùng chung nhãn với `price_cells_by_source` — "giá ẩn"/"lỗi
    giá"/"không có SP", xem `_site_status`).

    `url` LUÔN lấy từ catalog (`CatalogItem.source_url`) — link vào trang sản
    phẩm có sẵn ngay từ catalog, không phụ thuộc crawl giá đã thành công hay
    chưa (khác trước đây lấy từ `DrugPrice.source_url`, chỉ có sau khi
    live-fetch ra giá). Field rỗng hiện '—'.

    `records` RỖNG HOÀN TOÀN (chưa crawl lần nào — vd mở từ bảng TÌM thuốc,
    hoặc sản phẩm 'Đã chọn' còn đang crawl dở) → nhãn 'lỗi giá' của
    `_site_status` bị đổi thành 'chưa update' (đúng bản chất: CHƯA THỬ, không
    phải THỬ RỒI LỖI). Có ít nhất 1 record (crawl đã chạy, dù site khác có thể
    vẫn lỗi) → giữ nguyên 'lỗi giá' như cũ."""
    catalog_sources = {it.source for it in items}
    by_source: dict[SourceName, DrugPrice] = {}
    for r in records:
        _ = by_source.setdefault(r.source, r)
    best = cheapest(records)
    never_crawled = not records
    url_by_source: dict[SourceName, str] = {}
    for it in items:
        url_by_source.setdefault(it.source, it.source_url)

    rows: list[ProductDetailRow] = []
    for site in sites:
        source_value = site["source"]
        source = (
            source_value
            if isinstance(source_value, SourceName)
            else SourceName(source_value)
        )
        label, rec = _site_status(site, catalog_sources, by_source, best)
        if never_crawled and label == "lỗi giá":
            label = "chưa update"
        is_ok = rec is not None and rec.price_vnd > 0
        kind = status_kind(label)
        status = {
            "best": "Tốt nhất",
            "price": "Có giá",
            "out": "Hết hàng",
            "error": "Lỗi giá",
            "missing": "Không có SP",
            "hidden": "Giá ẩn",
            "pending": "Chưa cập nhật",
        }[kind]
        updated = (
            rec.crawled_at.strftime("%H:%M %d/%m/%Y")
            if rec is not None and rec.crawled_at
            else "—"
        )
        rows.append(
            {
                "site": site["name"],
                "price": label if is_ok else "—",
                "status": status,
                "updated": updated,
                "url": url_by_source.get(source) or "—",
            }
        )
    return rows


def cheapest_label(records: list[DrugPrice]) -> str:
    """Nhãn cột 'Rẻ nhất': 'nguồn: giá' hoặc rỗng nếu mọi giá đều ẩn."""
    best = cheapest(records)
    if best is None:
        return ""
    price = best.price_display or f"{best.price_vnd:,}đ"
    return f"★ {_source_of(best)} · {price}"


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
        out.append(
            {
                "drug_name": item.drug_name,
                "source": item.source.value
                if hasattr(item.source, "value")
                else str(item.source),
                "price": price_str,
                "last_checked": ts,
                "status": status,
                "image_url": item.image_url,
            }
        )
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

"""MainWindow — GUI tkinter cho PharmaPrice.

Ví von: tkinter là "quầy lễ tân" chỉ nói được một luồng (UI thread), còn
crawl là "việc bếp núc" chạy asyncio. Không thể để lễ tân xuống bếp (block UI),
sẽ crawl chạy ở thread riêng và gửi tin nhắn về qua một khay (queue.Queue);
lễ tân định kỳ ra lấy khay (after 100ms) để cập nhật log/progress.

Luồng mới:
- Gõ tên thuốc → gợi ý từ catalog_master.xlsx (nạp tĩnh 1 lần
  trong thread nền lúc khởi động, xem `_start_catalog_warmup`/
  `utils.catalog_master.load_master_catalog` — không còn tự crawl/refresh catalog
  từng site nữa; gõ tìm trước khi nạp xong sẽ không có gợi ý cho tới khi xong).
- Chọn thuốc → đưa vào danh sách đã chọn (mỗi thuốc 1 dòng, gộp giá nhiều nguồn).
- Danh sách đã chọn tự lưu ra output/selected_products.json sau mỗi lần
  thêm/xóa (xem `utils.selected_store`) — mở lại app hiện ngay bằng giá đã lưu,
  KHÔNG tự fetch live lại (tránh dội request vào nhiều site cùng lúc).
- "Xuất Excel" ghi toàn bộ bản ghi đã chọn ra file.
"""

from __future__ import annotations

import asyncio
import queue
import re
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import sv_ttk

from crawlers.b2b import CRAWLER_REGISTRY
from crawlers.base import AUTH_FAILURE_MARKER, clear_auth_cache
from crawlers.engine import CrawlerEngine, DuplicateProductNameError
from gui import viewmodel as vm
from utils.config_loader import load_sites, update_credentials
from utils.excel_writer import writer_for
from utils.models import CatalogItem, StockStatus
from utils.normalizer import strip_accents
from utils.selected_store import load_selected, save_selected
from utils.url_detect import detect_product_id, suggest_name_from_urls


def _auth_key(text: str) -> str:
    """Chuẩn hoá 1 định danh site về khoá chung để chống popup lỗi đăng nhập lặp.

    Cùng 1 site nhưng xuất hiện 3 dạng: `site_id` ("thuocsisaigon"), tên hiển thị
    ("Thuốc Sĩ Sài Gòn"), và prefix log `[ThuocSiSaiGon]`. Bỏ dấu + lowercase +
    chỉ giữ chữ-số → cả 3 hội tụ về "thuocsisaigon", dedup được xuyên suốt."""
    return "".join(ch for ch in strip_accents(text).lower() if ch.isalnum())


# sv_ttk chỉ theme hoá ttk.* — Listbox/Text là widget Tk cổ điển, tự set màu
# khớp bảng màu light của sv_ttk (nền trắng ngà, chữ đen, xanh accent Windows
# 11 khi chọn) để không bị lệch tông giữa 2 loại widget.
_CLASSIC_WIDGET_COLORS = {
    "background": "#fafafa",
    "foreground": "#1a1a1a",
    "selectbackground": "#0078d4",
    "selectforeground": "#ffffff",
    "relief": "flat",
    "highlightthickness": 1,
    "highlightbackground": "#d1d1d1",
    "highlightcolor": "#0078d4",
}

# Màu cho nút chức năng — CÙNG LÝ DO `_CLASSIC_WIDGET_COLORS`: ttk.Button dưới
# theme sv_ttk vẽ bằng ảnh bo góc, `style.configure(background=...)` không ăn
# — phải dùng tk.Button cổ điển (xem `_colored_button`) mới đổi màu được thật.
_BUTTON_COLORS = {
    "danger": {
        "bg": "#c0392b",
        "activebackground": "#e0473a",
        "fg": "#ffffff",
        "activeforeground": "#ffffff",
    },
    "success": {
        "bg": "#1e8449",
        "activebackground": "#27ae60",
        "fg": "#ffffff",
        "activeforeground": "#ffffff",
    },
    "primary": {
        "bg": "#0078d4",
        "activebackground": "#2b88d8",
        "fg": "#ffffff",
        "activeforeground": "#ffffff",
    },
}

# Semantic palette cho trạng thái giá. Mỗi màu luôn đi cùng nhãn/ký hiệu chữ
# (xem `gui.viewmodel.price_cell_display`) để người dùng không phải dựa riêng
# vào khả năng phân biệt màu. Foreground/background đều có contrast cao trên
# light theme hiện tại.
_STATUS_COLORS = {
    "best": {"background": "#dff3e7", "foreground": "#0b5d34"},
    "price": {"background": "#edf1f4", "foreground": "#25313c"},
    "error": {"background": "#fde7e7", "foreground": "#8f1d1d"},
    "missing": {"background": "#f0f1f2", "foreground": "#4c5661"},
    "out": {"background": "#fff0d6", "foreground": "#7a4300"},
    "hidden": {"background": "#fff7cc", "foreground": "#6f4d00"},
    "pending": {"background": "#e8f1fb", "foreground": "#285578"},
}

_STATUS_LEGEND = (
    ("best", "★ Tốt nhất"),
    ("price", "Giá thường"),
    ("error", "! Lỗi giá"),
    ("missing", "— Không có SP"),
    ("out", "× Hết hàng"),
)


class StatusCellTreeview(ttk.Treeview):
    """Treeview có nền semantic riêng cho từng ô trạng thái.

    Tk/ttk chỉ hỗ trợ ``tag_configure`` theo *cả dòng*. Bảng chính lại có một
    cột cho mỗi nhà thuốc, nên cùng một dòng có thể đồng thời chứa giá tốt nhất,
    giá thường, lỗi giá, hết hàng và không có sản phẩm. Tô cả dòng sẽ làm sai
    nghĩa. Lớp này giữ Treeview gốc làm nền cho selection/keyboard/scroll rồi
    đặt ``tk.Label`` đúng ``bbox`` của từng cell trạng thái.
    """

    _FORWARDED_POINTER_EVENTS = (
        "<Button-1>",
        "<Double-Button-1>",
        "<Button-3>",
    )

    def __init__(
        self,
        master,
        *,
        status_columns: tuple[str, ...],
        status_font: tuple[str, int],
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self._status_columns = status_columns
        self._status_font = status_font
        self._cell_overlays: dict[tuple[str, str], tk.Label] = {}
        self._overlay_redraw_job: str | None = None
        for sequence in (
            "<Configure>",
            "<Expose>",
            "<Motion>",
            "<ButtonRelease-1>",
            "<MouseWheel>",
            "<Button-4>",
            "<Button-5>",
        ):
            self.bind(sequence, lambda _event: self._schedule_overlay_redraw(), add="+")

    def insert(self, parent, index, iid=None, **kwargs):
        item_id = super().insert(parent, index, iid=iid, **kwargs)
        self._schedule_overlay_redraw()
        return item_id

    def delete(self, *items) -> None:
        doomed = set(items)
        super().delete(*items)
        for key, overlay in list(self._cell_overlays.items()):
            if key[0] in doomed:
                overlay.destroy()
                del self._cell_overlays[key]
        self._schedule_overlay_redraw()

    def set(self, item, column=None, value=None):
        result = super().set(item, column, value)
        if value is not None:
            self._schedule_overlay_redraw()
        return result

    def xview(self, *args):
        result = super().xview(*args)
        if args:
            self._schedule_overlay_redraw()
        return result

    def yview(self, *args):
        result = super().yview(*args)
        if args:
            self._schedule_overlay_redraw()
        return result

    def _schedule_overlay_redraw(self) -> None:
        if self._overlay_redraw_job is not None:
            return
        try:
            self._overlay_redraw_job = self.after_idle(self._redraw_cell_overlays)
        except tk.TclError:
            self._overlay_redraw_job = None

    def _redraw_cell_overlays(self) -> None:
        self._overlay_redraw_job = None
        try:
            rows = self.get_children("")
        except tk.TclError:
            return

        visible_keys: set[tuple[str, str]] = set()
        for item_id in rows:
            for column in self._status_columns:
                bbox = self.bbox(item_id, column)
                if not bbox:
                    continue
                value = str(self.set(item_id, column))
                kind = vm.status_kind(value)
                colors = _STATUS_COLORS[kind]
                key = (item_id, column)
                overlay = self._cell_overlays.get(key)
                if overlay is None:
                    overlay = self._make_cell_overlay(item_id, column)
                    self._cell_overlays[key] = overlay
                font_size = (
                    max(8, self._status_font[1] - 1)
                    if kind == "missing"
                    else self._status_font[1]
                )
                font = (
                    self._status_font[0],
                    font_size,
                    "bold"
                    if kind in {"best", "price", "error", "out"}
                    else "normal",
                )
                overlay.configure(
                    text=value,
                    background=colors["background"],
                    foreground=colors["foreground"],
                    font=font,
                )
                x, y, width, height = bbox
                overlay.place(
                    x=x + 1,
                    y=y + 1,
                    width=max(1, width - 2),
                    height=max(1, height - 2),
                )
                overlay.lift()
                visible_keys.add(key)

        for key, overlay in self._cell_overlays.items():
            if key not in visible_keys:
                overlay.place_forget()

    def _make_cell_overlay(self, item_id: str, column: str) -> tk.Label:
        overlay = tk.Label(
            self,
            anchor="center",
            borderwidth=0,
            padx=3,
            pady=0,
            takefocus=False,
        )
        for sequence in self._FORWARDED_POINTER_EVENTS:
            overlay.bind(
                sequence,
                lambda event, seq=sequence, widget=overlay: self._forward_pointer_event(
                    seq, event, widget
                ),
            )
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            overlay.bind(
                sequence,
                lambda event, seq=sequence, widget=overlay: self._forward_pointer_event(
                    seq, event, widget
                ),
            )
        return overlay

    def _forward_pointer_event(
        self, sequence: str, event: tk.Event, overlay: tk.Label
    ) -> str:
        """Chuyển event từ label phủ về Treeview để không mất tương tác cũ."""
        x = overlay.winfo_x() + event.x
        y = overlay.winfo_y() + event.y
        options: dict[str, int] = {"x": x, "y": y}
        delta = getattr(event, "delta", 0)
        if delta:
            options["delta"] = delta
        self.event_generate(sequence, **options)
        self._schedule_overlay_redraw()
        return "break"


class MainWindow(tk.Tk):
    # Ứng viên font theo thứ tự ưu tiên — "Segoe UI" (Windows chuẩn) trước,
    # sau đó các font Vietnamese-capable phổ biến trên Linux. Chọn font ĐẦU
    # TIÊN thật sự có trên máy (xem `_configure_fonts`).
    _FONT_CANDIDATES = (
        "Segoe UI",
        "Noto Sans",
        "Ubuntu",
        "DejaVu Sans",
        "Arial",
        "Helvetica",
    )

    def __init__(self):
        super().__init__()
        self.title("PharmaPrice")
        self.geometry("780x780")
        self.minsize(720, 720)
        self._set_window_icon()

        # sv_ttk: theme kiểu Windows 11 (Fluent) cho toàn bộ widget ttk — thay
        # cho "clam" trước đây. Cũng vẽ thuần Tcl (không qua GTK pixmap) nên
        # vẫn giữ được lợi ích chống nhấp nháy của "clam", chỉ đẹp hơn nhiều.
        try:
            sv_ttk.set_theme("light")
        except tk.TclError:
            pass
        self._configure_fonts()

        self._msg_queue: queue.Queue = queue.Queue()

        # Engine dài hạn cho cache-read (suggest / find) — đọc SQLite từ UI thread.
        # Worker thread sẽ tự tạo engine riêng khi crawl.
        try:
            self._engine = CrawlerEngine(use_cache=True)
        except Exception as exc:
            messagebox.showwarning(
                "Thiếu cấu hình",
                f"Không đọc được config/accounts.yaml:\n{exc}\n\n"
                "Copy config/accounts.example.yaml → config/accounts.yaml và điền tài khoản.",
            )
            self._engine = None

        self._sites = self._safe_load_sites()
        self._selected: dict[str, list] = {}  # canonical_name -> list[DrugPrice]
        self._groups: dict[
            str, list[CatalogItem]
        ] = {}  # canonical_name -> [catalog item]
        self._catalog_items: dict[
            str, list[CatalogItem]
        ] = {}  # canonical_name -> catalog items dùng để fetch
        self._pending: set[str] = (
            set()
        )  # tên đang chờ fetch giá live, chặn thêm trùng khi worker chưa xong
        self._cancelled: set[str] = (
            set()
        )  # tên bị xóa thủ công lúc còn đang crawl — bỏ qua kết quả trễ
        self._suggest_job: str | None = (
            None  # id `after()` debounce gợi ý, hủy job cũ khi gõ tiếp
        )
        self._catalog_ready = (
            False  # True sau khi _warm_catalog_worker nạp xong catalog_master.xlsx
        )
        self._recrawling = (
            False  # True trong lúc _recrawl_all_worker đang chạy, chặn bấm chồng
        )
        self._saving_manual_product = (
            False  # True trong lúc _save_manual_product_worker đang ghi xlsx
        )
        self._edit_listing_window: tk.Toplevel | None = None
        self._product_detail_window: tk.Toplevel | None = None
        self._auth_warned: set[str] = (
            set()
        )  # site đã popup lỗi đăng nhập rồi — không popup lại (chỉ log)
        self._build_ui()
        self._restore_selected()
        self.after(100, self._drain_queue)
        self._start_catalog_warmup()
        self._start_login_check()

    # ----------------------------------------------------------------- fonts
    def _set_window_icon(self) -> None:
        """Icon viên nang cho cửa sổ. Asset bundle trong .exe (_MEIPASS) hoặc
        assets/ ở gốc project khi chạy dev. Giữ tham chiếu PhotoImage (self._icon)
        để Tk không thu hồi (GC) làm mất icon. Lỗi icon KHÔNG được làm sập app."""
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        png = base / "assets" / "pill.png"
        try:
            self._icon = tk.PhotoImage(file=str(png))
            self.iconphoto(True, self._icon)
        except Exception:  # noqa: BLE001 — icon là trang trí, thiếu cũng chạy được
            pass

    def _configure_fonts(self) -> None:
        """sv_ttk (theme Windows 11 Fluent) tạo font "SunValleyBodyFont"/
        "SunValleyCaptionFont" với family "Segoe UI Variable ..." — font NÀY chỉ
        có sẵn trên Windows 11 bản mới nhất, không có trên Linux hay Windows cũ
        hơn. Khi family không tồn tại, Tk âm thầm thay bằng font khác (không rõ
        nguồn, có thể quá nhỏ/thiếu dấu tiếng Việt) — đây là nguyên nhân "vỡ
        font" thay vì do code hiển thị sai. Chọn font ĐẦU TIÊN thật sự có trên
        máy (`_FONT_CANDIDATES`), đè lên 2 font sv_ttk có dùng thật (Treeview/
        Entry/Heading/LabelFrame — xem sv_ttk `light.tcl`/`sv.tcl`) + font Tk
        mặc định (Button/Label/menu dùng, Listbox/Text không theo theme ttk nên
        phải set `font=self._ui_font` riêng lúc tạo widget)."""
        available = set(tkfont.families(self))
        family = next((f for f in self._FONT_CANDIDATES if f in available), None)
        base_size = 10

        for name in (
            "SunValleyCaptionFont",
            "SunValleyBodyFont",
            "TkDefaultFont",
            "TkTextFont",
            "TkHeadingFont",
            "TkMenuFont",
        ):
            try:
                f = tkfont.nametofont(name)
            except tk.TclError:
                continue
            f.configure(family=family or f.actual("family"), size=base_size)

        self._ui_font = (
            family or tkfont.nametofont("TkDefaultFont").actual("family"),
            base_size,
        )

    # ----------------------------------------------------------------- config
    def _safe_load_sites(self) -> dict:
        try:
            return load_sites()
        except Exception:
            return {}

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 4}

        title_row = ttk.Frame(self)
        title_row.pack(fill="x", **pad)
        ttk.Label(title_row, text="PharmaPrice", font=("", 13, "bold")).pack(
            side="left"
        )
        self._credentials_btn = ttk.Button(
            title_row, text="Sửa tài khoản", command=self._open_credentials_editor
        )
        self._credentials_btn.pack(side="right")

        # 2 tab riêng: Kết quả (search+chọn+export) tách khỏi Log, để khỏi
        # phải cuộn qua nửa màn hình mới thấy log.
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        results_tab = ttk.Frame(notebook)
        log_tab = ttk.Frame(notebook)
        notebook.add(results_tab, text="Tìm & Kết quả")
        notebook.add(log_tab, text="Log")

        self._build_results_tab(results_tab)
        self._build_log_tab(log_tab)

    def _build_results_tab(self, parent: ttk.Frame) -> None:
        pad = {"padx": 10, "pady": 4}

        # --- Search row ---
        search_row = ttk.Frame(parent)
        search_row.pack(fill="x", **pad)
        ttk.Label(search_row, text="Tìm thuốc:").pack(side="left")
        self._search = ttk.Entry(search_row)
        self._search.pack(side="left", fill="x", expand=True, padx=6)
        self._search.bind(
            "<KeyRelease>", lambda _e: self._schedule_refresh_suggestions()
        )
        # Spinner khi catalog chưa nạp xong — dùng ttk.Progressbar (vẽ bằng theme,
        # KHÔNG phải ký tự Unicode) vì spinner kiểu chữ (vd Braille "⠋⠙⠹...") có
        # thể không hiện được nếu font đang chọn thiếu glyph đó (tuỳ máy) — im
        # lặng thành ô trống, user tưởng app treo. Progressbar luôn vẽ được, không
        # phụ thuộc font. Ẩn hẳn (không chỉ để rỗng) khi catalog đã sẵn sàng.
        self._catalog_spinner_label = ttk.Label(search_row, text="Đang tải catalog...")
        self._catalog_progress = ttk.Progressbar(
            search_row, mode="indeterminate", length=90
        )

        sug_frame = ttk.Frame(parent)
        sug_frame.pack(fill="x", padx=10)
        # activestyle="none": Listbox là widget Tk cổ điển (không qua ttk theme),
        # mặc định gạch chân/tô item đang hover → redraw thừa mỗi khi rê chuột,
        # dễ nhấp nháy trên X server không có compositor.
        self._suggestions = tk.Listbox(
            sug_frame,
            height=8,
            activestyle="none",
            exportselection=False,
            font=self._ui_font,
            **_CLASSIC_WIDGET_COLORS,
        )
        sug_scroll = ttk.Scrollbar(sug_frame, command=self._suggestions.yview)
        self._suggestions.configure(yscrollcommand=sug_scroll.set)
        self._suggestions.pack(side="left", fill="both", expand=True)
        sug_scroll.pack(side="right", fill="y")
        self._suggestions.bind("<Double-Button-1>", lambda _e: self._add_selected())
        self._suggestions.bind("<Control-c>", self._copy_listbox_selection)
        self._suggestions.bind("<Control-C>", self._copy_listbox_selection)
        self._suggestions.bind("<Button-3>", self._on_suggestion_right_click)

        add_row = ttk.Frame(parent)
        add_row.pack(fill="x", padx=10)
        self._add_btn = self._colored_button(
            add_row, "Thêm", self._add_selected, kind="success"
        )
        self._add_btn.pack(side="left")
        self._add_product_btn = self._colored_button(
            add_row, "Thêm sản phẩm mới", self._open_add_product_dialog, kind="success"
        )
        self._add_product_btn.pack(side="left", padx=6)

        # --- Selected list ---
        # Cột "Giá theo nguồn" trước đây gộp cả 9 site vào 1 chuỗi dài — giờ
        # tách hẳn 9 cột riêng (1 cột/site, header = tên site) cho dễ so sánh,
        # đổi lại bảng rộng hơn cửa sổ nên cần thanh cuộn ngang. Có thêm cột STT
        # (đánh lại mỗi khi thêm/xóa dòng, xem `_renumber_tree`) và bấm chuột
        # phải vào 1 dòng để xem chi tiết (xem `_on_tree_right_click`).
        ttk.Label(parent, text="Đã chọn:", font=("", 10, "bold")).pack(
            anchor="w", padx=10, pady=(6, 0)
        )
        legend_row = ttk.Frame(parent)
        legend_row.pack(fill="x", padx=10, pady=(4, 2))
        for kind, label in _STATUS_LEGEND:
            colors = _STATUS_COLORS[kind]
            tk.Label(
                legend_row,
                text=label,
                background=colors["background"],
                foreground=colors["foreground"],
                font=(self._ui_font[0], 9, "bold"),
                padx=7,
                pady=3,
                borderwidth=0,
            ).pack(side="left", padx=(0, 5))
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=2)
        site_order = self._site_order()
        site_ids = tuple(sid for sid, _, _ in site_order)
        self._tree = StatusCellTreeview(
            tree_frame,
            status_columns=site_ids,
            status_font=self._ui_font,
            columns=("stt", "refresh", "name", "cheapest") + site_ids,
            show="headings",
            height=10,
        )
        self._tree.heading("stt", text="STT")
        self._tree.heading("refresh", text="↻")
        self._tree.heading("name", text="Tên thuốc")
        self._tree.heading("cheapest", text="Rẻ nhất ★")
        self._tree.column("stt", width=40, anchor="center", stretch=False)
        self._tree.column("refresh", width=36, anchor="center", stretch=False)
        self._tree.column("name", width=170, anchor="w")
        self._tree.column("cheapest", width=155, anchor="w")
        for sid, _source, display_name in site_order:
            self._tree.heading(sid, text=display_name)
            # Đủ chỗ cho trạng thái dài nhất: "× Hết hàng · 109.900đ".
            # Bảng đã có thanh cuộn ngang nên ưu tiên không cắt mất giá.
            self._tree.column(sid, width=165, anchor="center")
        self._tree.bind("<Button-3>", self._on_tree_right_click)
        # Nhấp đúp cũng mở bảng chi tiết (giống chuột phải) — bảng chi tiết có
        # sẵn nút sửa/xóa từng site (nhấp đúp dòng site trong đó, xem
        # `_on_detail_row_double_click`/`_open_edit_listing_dialog`).
        self._tree.bind("<Double-Button-1>", self._on_tree_double_click)
        # Bấm cột "↻" ở 1 dòng → crawl lại LIVE riêng dòng đó (xem
        # `_on_tree_left_click`/`_refresh_one`) — KHÔNG return "break" nên
        # hành vi chọn dòng mặc định của Treeview vẫn chạy bình thường.
        self._tree.bind("<Button-1>", self._on_tree_left_click)
        # with_menu=False: right-click ở bảng này đã dùng để mở bảng chi tiết
        # (_on_tree_right_click) — chỉ thêm Ctrl+C, không thêm menu chuột phải
        # để khỏi tranh nhau với hành vi đó.
        self._bind_tree_copy(self._tree, with_menu=False)

        vbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        hbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vbar.set, xscrollcommand=hbar.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        sel_row = ttk.Frame(parent)
        sel_row.pack(fill="x", padx=10, pady=(0, 10))
        self._remove_btn = self._colored_button(
            sel_row, "Xóa dòng", self._remove_selected, kind="danger"
        )
        self._remove_btn.pack(side="left")
        self._clear_btn = self._colored_button(
            sel_row, "Xóa hết", self._clear_all, kind="danger"
        )
        self._clear_btn.pack(side="left", padx=6)
        self._recrawl_btn = self._colored_button(
            sel_row, "Crawl lại tất cả", self._recrawl_all, kind="primary"
        )
        self._recrawl_btn.pack(side="left", padx=6)
        self._export_excel_btn = self._colored_button(
            sel_row, "Xuất Excel", self._export_excel, kind="primary"
        )
        self._export_excel_btn.pack(side="right", padx=6)

    def _build_log_tab(self, parent: ttk.Frame) -> None:
        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Button(btn_row, text="Copy toàn bộ log", command=self._copy_log).pack(
            side="left"
        )

        log_frame = ttk.Frame(parent)
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self._log = tk.Text(
            log_frame,
            wrap="word",
            state="disabled",
            font=self._ui_font,
            **_CLASSIC_WIDGET_COLORS,
        )
        log_scroll = ttk.Scrollbar(log_frame, command=self._log.yview)
        self._log.configure(yscrollcommand=log_scroll.set)
        self._log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    def _copy_log(self) -> None:
        """Copy toàn bộ nội dung tab Log vào clipboard — tiện dán ra ngoài để
        debug (report lỗi, hỏi AI, gửi cho người khác)."""
        content = self._log.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(content)
        self._append_log("Đã copy toàn bộ log vào clipboard.")

    # ------------------------------------------------------- suggestions catalog
    def _start_catalog_warmup(self) -> None:
        """Nạp catalog_master.xlsx (~58k dòng, vài chục giây) trong
        thread nền ngay lúc mở app, để lúc user gõ tìm lần đầu không bị đứng UI chờ
        đọc file. `_refresh_suggestions` tự bỏ qua cho tới khi có tin "catalog_ready"."""
        if self._engine is None:
            return
        self._append_log(
            "Đang tải catalog sản phẩm chuẩn (có thể mất khoảng 1 phút)..."
        )
        self._catalog_spinner_label.pack(side="left", padx=(6, 4))
        self._catalog_progress.pack(side="left")
        self._catalog_progress.start(12)
        threading.Thread(target=self._warm_catalog_worker, daemon=True).start()

    def _stop_catalog_spinner(self) -> None:
        """Gọi khi có tin 'catalog_ready' — dừng animation + ẨN HẲN (pack_forget,
        không chỉ để rỗng) 2 widget spinner, khỏi choán chỗ vô ích sau khi xong."""
        self._catalog_progress.stop()
        self._catalog_progress.pack_forget()
        self._catalog_spinner_label.pack_forget()

    def _warm_catalog_worker(self) -> None:
        try:
            count = self._engine.warm_master_catalog()
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI tải catalog: {exc}"))
            # Dừng spinner dù lỗi — không thì nó xoay vô thời hạn vì
            # `_catalog_ready` không bao giờ bật lên (đúng, vì catalog thật sự
            # chưa sẵn sàng), nhưng user không cần thấy nó xoay mãi vô nghĩa.
            self._msg_queue.put(("catalog_failed", None))
            return
        self._msg_queue.put(("catalog_ready", count))

    # -------------------------------------------------- kiểm tra đăng nhập
    def _start_login_check(self) -> None:
        """Ngay khi mở app, thử đăng nhập tất cả site trong thread nền. Site nào
        lỗi (sai tài khoản/hết hạn) sẽ được gộp thành 1 popup cảnh báo — để người
        dùng biết NGAY, thay vì crawl xong mới thấy giá = 0 (vd thuocsisaigon)."""
        if self._engine is None:
            return
        self._append_log("Đang kiểm tra đăng nhập các site...")
        threading.Thread(target=self._login_check_worker, daemon=True).start()

    def _login_check_worker(self) -> None:
        # Engine riêng cho thread này (network I/O) — bỏ marker khỏi log để
        # KHÔNG kích popup tự động của _drain_queue; startup check tự gộp 1 popup
        # riêng qua "login_check_done".
        def qlog(m: str) -> None:
            self._msg_queue.put(
                (
                    "log",
                    m.replace(AUTH_FAILURE_MARKER + ": ", "").replace(
                        AUTH_FAILURE_MARKER, "[!]"
                    ),
                )
            )

        try:
            engine = CrawlerEngine(use_cache=False, log=qlog)
            results = asyncio.run(engine.check_logins())
        except Exception as exc:  # noqa: BLE001
            self._msg_queue.put(("log", f"Lỗi kiểm tra đăng nhập: {exc}"))
            return
        self._msg_queue.put(("login_check_done", results))

    def _schedule_refresh_suggestions(self) -> None:
        """Debounce gõ phím: mỗi `_refresh_suggestions()` query SQLite + fuzzy-merge
        catalog + dựng lại cả Listbox — gõ nhanh mà chạy đồng bộ ngay mỗi phím sẽ
        khựng UI 1 nhịp mỗi ký tự (cảm giác nhấp nháy). Hủy job cũ, chỉ chạy thật
        150ms sau ký tự cuối cùng."""
        if self._suggest_job is not None:
            self.after_cancel(self._suggest_job)
        self._suggest_job = self.after(150, self._refresh_suggestions)

    def _refresh_suggestions(self) -> None:
        """Gợi ý theo tên chuẩn, dựa trên catalog_master.xlsx (tên+SKU
        thật của từng site, đã gộp nhóm sẵn — không phải cache giá cũ từ lịch sử
        search), xem `crawlers.engine.CrawlerEngine.suggest_catalog`. Bỏ qua nếu
        catalog chưa nạp xong (`_warm_catalog_worker`) — đọc ~58k dòng xlsx mất vài
        chục giây, KHÔNG được để UI thread tự đọc đồng bộ khi user gõ trước lúc đó."""
        self._suggest_job = None
        if self._engine is None or not self._catalog_ready:
            return
        prefix = self._search.get().strip()
        try:
            items = self._engine.suggest_catalog(prefix, limit=200)
            self._groups = vm.build_catalog_groups(items)
        except Exception as exc:
            self._append_log(f"Lỗi đọc catalog gợi ý: {exc}")
            return
        self._suggestions.delete(0, "end")
        for n in vm.suggest(self._groups, prefix, limit=30):
            self._suggestions.insert("end", n)

    # --------------------------------------------------------- selected list
    def _restore_selected(self) -> None:
        """Nạp lại danh sách 'Đã chọn' từ lần trước (output/selected_products.json)
        NGAY lúc mở app — hiện luôn bằng giá đã lưu, KHÔNG fetch live lại (tránh
        dội request vào 9 site cùng lúc nếu danh sách dài — đúng lo ngại rate-limit
        khi restore nhiều sản phẩm cùng lúc). Sau khi catalog sẵn sàng, dữ liệu
        legacy từng lấy theo tên sẽ được nhận diện và crawl lại tuần tự đúng một
        lần; dữ liệu đã khớp ID thì vẫn giữ nguyên, người dùng chủ động refresh."""
        try:
            selected, catalog_items = load_selected()
        except Exception as exc:
            self._append_log(f"Lỗi đọc danh sách đã lưu: {exc}")
            return
        if not selected:
            return
        site_descriptors = self._site_descriptors()
        for name, records in selected.items():
            items = catalog_items.get(name, [])
            self._selected[name] = records
            self._catalog_items[name] = items
            cells = vm.price_cells_by_source(site_descriptors, items, records)
            self._tree.insert(
                "",
                "end",
                iid=name,
                values=("", "↻", name, vm.cheapest_label(records), *cells),
            )
        self._renumber_tree()
        self._append_log(
            f"Đã khôi phục {len(selected)} sản phẩm từ lần trước "
            "(giá lúc thoát ứng dụng — có thể đã cũ, xóa rồi thêm lại để lấy giá mới)."
        )

    def _persist_selected(self) -> None:
        """Ghi lại danh sách 'Đã chọn' hiện tại ra file — gọi sau MỖI lần
        thêm/xóa để tắt app bất cứ lúc nào cũng không mất danh sách."""
        try:
            save_selected(self._selected, self._catalog_items)
        except Exception as exc:
            self._append_log(f"Lỗi lưu danh sách đã chọn: {exc}")

    def _add_selected(self) -> None:
        """Chọn 1 gợi ý → lấy giá LIVE ngay lúc này (không đọc cache giá cũ) —
        chạy nền để không treo UI trong lúc chờ site trả kết quả."""
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        sel = self._suggestions.curselection()
        if not sel:
            messagebox.showinfo("Chưa chọn", "Chọn một thuốc trong danh sách gợi ý.")
            return
        name = self._suggestions.get(sel[0])
        items = self._groups.get(name, [])
        if not items:
            messagebox.showinfo(
                "Không có dữ liệu", f"Catalog không có sản phẩm cho '{name}'."
            )
            return
        self._start_add(name, items)

    # ------------------------------------------------------ thêm sản phẩm mới
    def _open_add_product_dialog(self) -> None:
        """Dialog thêm 1 sản phẩm CHƯA CÓ trong catalog: dán URL cho tối đa 9 site
        (bỏ trống site không bán) → 'Xác nhận' tách product_id CƠ HỌC từ URL (xem
        `utils.url_detect`, không gọi mạng nên tức thời) → bước 2 kiểm tra crawl
        LIVE thật + sửa tên/link (xem `_open_verify_new_product`) → 'Lưu' (sau
        khi xác nhận) mới ghi vào catalog_master.xlsx (xem
        `CrawlerEngine.add_manual_product`)."""
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        if self._saving_manual_product:
            messagebox.showinfo("Đang lưu", "Đang lưu sản phẩm trước đó — chờ xong đã.")
            return

        win = tk.Toplevel(self)
        win.transient(self)
        win.grab_set()
        self._render_product_url_form(win)

    def _render_product_url_form(
        self, win: tk.Toplevel, initial_urls: dict[str, str] | None = None
    ) -> None:
        """Vẽ bước nhập URL trong dialog thêm sản phẩm.

        Dùng lại cùng một ``Toplevel`` cho cả hai bước. ``initial_urls`` giúp
        nút "Quay lại" từ màn kiểm tra phục hồi nguyên các link người dùng đã
        dán/chỉnh, thay vì tạo form trống hoặc thêm một cửa sổ mới.
        """
        for widget in win.winfo_children():
            widget.destroy()
        urls = initial_urls or {}
        win.title("Thêm sản phẩm mới")
        self._center_toplevel(win, 640, 480)

        ttk.Label(
            win,
            text="Dán URL trang chi tiết sản phẩm cho các site có bán (bỏ trống site không có):",
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(10, 6))

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=12)
        entries: dict[str, ttk.Entry] = {}
        for row_i, (site_id, _source, name) in enumerate(self._site_order()):
            ttk.Label(body, text=f"{name}:").grid(
                row=row_i, column=0, sticky="w", padx=(0, 6), pady=3
            )
            entry = ttk.Entry(body, width=60)
            if urls.get(site_id):
                entry.insert(0, urls[site_id])
            entry.grid(row=row_i, column=1, sticky="we", pady=3)
            entries[site_id] = entry
        body.columnconfigure(1, weight=1)

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(8, 12))
        self._colored_button(
            btn_row,
            "Xác nhận",
            lambda: self._confirm_product_urls(entries, win),
            kind="primary",
        ).pack(side="right")
        ttk.Button(btn_row, text="Hủy", command=win.destroy).pack(side="right", padx=6)

    def _confirm_product_urls(
        self, entries: dict[str, ttk.Entry], win: tk.Toplevel
    ) -> None:
        """Tách product_id cho từng ô có URL — site không tách được (URL sai định
        dạng/thiếu phần cần thiết) bị bỏ qua, KHÔNG chặn các site còn lại (đã xác
        nhận với user). Không site nào tách được → báo lỗi, giữ dialog để sửa lại."""
        urls = {site_id: entry.get().strip() for site_id, entry in entries.items()}
        detected: dict[str, str] = {}
        for site_id, url in urls.items():
            if url and detect_product_id(site_id, url):
                detected[site_id] = url

        if not detected:
            messagebox.showerror(
                "Không nhận diện được",
                "Không tách được ID sản phẩm từ URL nào đã dán.\n"
                "Kiểm tra lại URL (phải là link trang CHI TIẾT sản phẩm thật) rồi thử lại.",
            )
            return

        suggested_name = suggest_name_from_urls(list(urls.values()))
        self._open_verify_new_product(detected, suggested_name, win)

    def _open_verify_new_product(
        self, detected: dict[str, str], suggested_name: str, win: tk.Toplevel
    ) -> None:
        """Bước 2 'Thêm sản phẩm mới': bảng kiểm tra GIỐNG bảng chi tiết
        (`_show_product_detail`) — thử crawl LIVE THẬT (xem
        `_verify_new_product_worker`) để kiểm tra độ chính xác trước khi lưu,
        cho sửa lại tên (ô trên) + link riêng từng site (nhấp đúp 1 dòng) NGAY
        trong bảng này. CHỈ ghi vào catalog khi bấm 'Lưu' — sau khi đã xem kết
        quả kiểm tra (xem `_confirm_save_new_product`)."""
        for widget in win.winfo_children():
            widget.destroy()
        win.title("Thêm sản phẩm mới — kiểm tra")
        self._center_toplevel(win, 760, 520)

        site_names = {sid: name for sid, _s, name in self._site_order()}
        # site_id -> url, SỬA TRỰC TIẾP dict này (chưa ghi catalog) khi user
        # nhấp đúp đổi link — `_run_check` luôn đọc lại dict MỚI NHẤT.
        urls: dict[str, str] = dict(detected)

        ttk.Label(win, text="Tên sản phẩm:").pack(anchor="w", padx=12, pady=(10, 2))
        name_entry = ttk.Entry(win)
        name_entry.insert(0, suggested_name)
        name_entry.pack(fill="x", padx=12, pady=(0, 8))

        ttk.Label(
            win,
            text="Đang kiểm tra giá thật từ từng site — nhấp đúp 1 dòng để sửa link.",
            foreground="#666666",
        ).pack(anchor="w", padx=12)

        tree_frame = ttk.Frame(win)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=(4, 8))
        tree = ttk.Treeview(
            tree_frame,
            columns=("site", "price", "status", "url"),
            show="headings",
            height=9,
        )
        self._configure_status_tags(tree)
        for col, text, width in (
            ("site", "Site", 130),
            ("price", "Giá", 100),
            ("status", "Trạng thái", 110),
            ("url", "Link", 320),
        ):
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor="w")
        vbar = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vbar.set)
        tree.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")
        self._bind_tree_copy(tree, with_menu=True)

        for site_id, url in urls.items():
            tree.insert(
                "",
                "end",
                iid=site_id,
                values=(site_names.get(site_id, site_id), "—", "đang kiểm tra...", url),
                tags=("pending",),
            )

        def _run_check() -> None:
            pairs: list[tuple[str, CatalogItem]] = []
            check_name = name_entry.get().strip() or suggested_name
            for site_id, url in urls.items():
                product_id = detect_product_id(site_id, url)
                source = getattr(CRAWLER_REGISTRY.get(site_id), "source_name", None)
                if not product_id or source is None:
                    continue
                pairs.append(
                    (
                        site_id,
                        CatalogItem(
                            product_id=product_id,
                            drug_name=check_name,
                            search_name=strip_accents(check_name).lower(),
                            source=source,
                            source_url=url,
                        ),
                    )
                )
            if not pairs:
                return
            threading.Thread(
                target=self._verify_new_product_worker,
                args=(tree, pairs),
                daemon=True,
            ).start()

        def _on_row_double_click(event: tk.Event) -> None:
            site_id = tree.identify_row(event.y)
            if not site_id:
                return
            current_url = urls.get(site_id, "")
            new_url = simpledialog.askstring(
                f"Link — {site_names.get(site_id, site_id)}",
                "Dán link mới cho site này:",
                initialvalue=current_url,
                parent=win,
            )
            if new_url is None:
                return
            new_url = new_url.strip()
            if not new_url or new_url == current_url:
                return
            if not detect_product_id(site_id, new_url):
                messagebox.showerror(
                    "Không nhận diện được",
                    "Không tách được ID sản phẩm từ link này.",
                    parent=win,
                )
                return
            urls[site_id] = new_url
            tree.set(site_id, "url", new_url)
            tree.set(site_id, "price", "—")
            tree.set(site_id, "status", "đang kiểm tra...")
            _run_check()

        tree.bind("<Double-Button-1>", _on_row_double_click)

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        self._colored_button(
            btn_row,
            "Lưu",
            lambda: self._confirm_save_new_product(name_entry, urls, win),
            kind="success",
        ).pack(side="right")
        ttk.Button(btn_row, text="Hủy", command=win.destroy).pack(side="right", padx=6)
        ttk.Button(
            btn_row,
            text="Quay lại",
            command=lambda: self._render_product_url_form(win, urls),
        ).pack(side="left", padx=(0, 6))
        self._colored_button(
            btn_row,
            "Kiểm tra lại",
            _run_check,
            kind="primary",
        ).pack(side="left")

        _run_check()

    def _verify_new_product_worker(
        self, tree: ttk.Treeview, pairs: list[tuple[str, CatalogItem]]
    ) -> None:
        """Crawl LIVE THẬT để kiểm tra độ chính xác trước khi lưu — KHÔNG ghi
        gì vào catalog (chỉ hiển thị). LUÔN gửi 'verify_crawl_done' (records
        rỗng nếu lỗi hẳn) để bảng không kẹt 'đang kiểm tra...' vĩnh viễn."""

        def log(msg: str) -> None:
            self._msg_queue.put(("log", msg))

        items = [it for _sid, it in pairs]
        try:
            engine = CrawlerEngine(log=log, use_cache=True)
            try:
                records = asyncio.run(engine.fetch_live_prices(items))
            finally:
                engine.close()
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI kiểm tra sản phẩm mới: {exc}"))
            records = []
        self._msg_queue.put(("verify_crawl_done", tree, pairs, records))

    def _confirm_save_new_product(
        self, name_entry: ttk.Entry, urls: dict[str, str], win: tk.Toplevel
    ) -> None:
        name = name_entry.get().strip()
        if not name:
            messagebox.showinfo(
                "Thiếu tên", "Nhập tên sản phẩm trước khi lưu.", parent=win
            )
            return
        if self._engine.product_name_exists(name):
            messagebox.showerror(
                "Tên bị trùng",
                f"Tên sản phẩm '{name}' đã có trong dữ liệu. Hãy nhập tên khác.",
                parent=win,
            )
            return
        if not messagebox.askyesno(
            "Lưu sản phẩm mới",
            f"Lưu '{name}' ({len(urls)} site) vào catalog?",
            parent=win,
        ):
            return
        win.destroy()
        self._saving_manual_product = True
        self._add_product_btn.configure(state="disabled")
        self._append_log(f"Đang lưu sản phẩm mới '{name}' vào catalog...")
        threading.Thread(
            target=self._save_manual_product_worker,
            args=(name, dict(urls)),
            daemon=True,
        ).start()

    def _save_manual_product_worker(self, name: str, urls: dict[str, str]) -> None:
        """LUÔN gửi 'manual_product_done' dù lỗi ở bước nào — thiếu bước này sẽ làm
        nút 'Thêm sản phẩm mới' kẹt 'disabled' vĩnh viễn, giống bài học đã sửa ở
        `_recrawl_all_worker`."""
        try:
            items = self._engine.add_manual_product(urls, name)
        except DuplicateProductNameError as exc:
            self._msg_queue.put(("manual_product_duplicate", name, str(exc)))
            return
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI lưu sản phẩm mới '{name}': {exc}"))
            self._msg_queue.put(("manual_product_done", name, 0))
            return
        self._msg_queue.put(("manual_product_done", name, len(items)))

    def _start_add(self, name: str, items: list[CatalogItem]) -> None:
        """Đuôi dùng chung sau khi chọn 1 gợi ý: check trùng (kể cả đang chờ fetch)
        → thêm NGAY vào 'Đã chọn' với trạng thái
        'đang crawl' (trước đây phải đợi fetch xong mới thấy dòng, bấm xong tưởng
        không ăn) → lấy giá live nền qua `_fetch_price_worker`, `_on_priced` sẽ
        cập nhật lại đúng dòng này khi có kết quả thật. Chặn `_pending` để tránh
        double-click bắn 2 thread cùng tên → 2 lần insert cùng iid → TclError."""
        if name in self._selected or name in self._pending:
            messagebox.showinfo(
                "Trùng", f"'{name}' đã có trong danh sách (hoặc đang lấy giá)."
            )
            return
        self._pending.add(name)
        self._selected[name] = []
        self._catalog_items[name] = items
        self._insert_crawling_row(name)
        self._append_log(f"Đang lấy giá live cho '{name}' ({len(items)} nguồn)...")
        threading.Thread(
            target=self._fetch_price_worker,
            args=(name, items),
            daemon=True,
        ).start()

    def _insert_crawling_row(self, name: str) -> None:
        """Dòng tạm 'đang crawl' cho đủ số cột (STT + ↻ + Tên thuốc + Rẻ nhất +
        9 site) — `_on_priced`/`_on_price_failed` sẽ xóa-và-chèn-lại đúng iid
        này khi có kết quả thật (xem `_tree.exists`/`_tree.delete` ở đó). STT để
        rỗng, `_renumber_tree()` đánh số lại ngay sau.

        Chèn lại ĐÚNG VỊ TRÍ cũ (`self._tree.index(name)`) nếu dòng đã tồn tại
        — trước đây luôn chèn ở cuối ("end"), khiến refresh 1 dòng (`_refresh_one`)
        hay crawl lại tất cả (`_recrawl_all`) làm xáo trộn thứ tự thuốc."""
        site_count = len(self._site_order())
        index = self._tree.index(name) if self._tree.exists(name) else "end"
        if self._tree.exists(name):
            self._tree.delete(name)
        self._tree.insert(
            "",
            index,
            iid=name,
            values=("", "↻", name, "đang crawl...", *(("đang crawl...",) * site_count)),
        )
        self._renumber_tree()

    def _renumber_tree(self) -> None:
        """STT không tự có trong Treeview — đánh lại từ 1 theo đúng thứ tự hiển
        thị mỗi khi thêm/xóa dòng, để xóa 1 dòng giữa thì các dòng sau lùi số
        chứ không để trống số."""
        for i, iid in enumerate(self._tree.get_children(), start=1):
            self._tree.set(iid, "stt", i)

    def _fetch_price_worker(self, name: str, items: list[CatalogItem]) -> None:
        """Chạy trong thread riêng: mở engine riêng, gọi fetch_live_prices (network),
        gửi kết quả về UI qua queue — không đụng widget từ thread khác."""

        def log(msg: str) -> None:
            self._msg_queue.put(("log", msg))

        try:
            engine = CrawlerEngine(log=log, use_cache=True)
            try:
                records = asyncio.run(engine.fetch_live_prices(items))
            finally:
                engine.close()
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI lấy giá live '{name}': {exc}"))
            self._msg_queue.put(("price_failed", name))
            return
        self._msg_queue.put(("priced", name, records, items))

    def _recrawl_all(self) -> None:
        """Crawl lại giá LIVE cho TOÀN BỘ sản phẩm đang có trong 'Đã chọn' — dùng
        khi mới mở app (danh sách khôi phục từ file, giá có thể cũ, xem
        `_restore_selected`) hoặc đơn giản muốn refresh giá mới nhất. Xử lý TUẦN
        TỰ từng sản phẩm một (không phải asyncio.gather cả loạt) để không dội
        request vào 9 site cùng lúc nếu danh sách dài — cùng tinh thần rate-limit
        đã xử lý ở `CrawlerEngine.fetch_live_prices` (gom theo site, 1 login/site)."""
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        if self._recrawling:
            messagebox.showinfo(
                "Đang crawl", "Đang crawl lại — chờ xong đã rồi bấm tiếp."
            )
            return
        names = [n for n in self._selected if n not in self._pending]
        if not names:
            messagebox.showinfo(
                "Danh sách trống", "Chưa có sản phẩm nào trong 'Đã chọn'."
            )
            return
        self._start_recrawl_names(
            names,
            f"Đang crawl lại giá cho {len(names)} sản phẩm "
            "(tuần tự, tránh rate-limit)...",
        )

    def _start_recrawl_names(self, names: list[str], message: str) -> None:
        """Khởi chạy worker tuần tự cho một tập tên đã chọn.

        Dùng chung cho nút Crawl lại tất cả và bước tự sửa dữ liệu legacy sau
        khi catalog sẵn sàng.
        """
        self._recrawling = True
        self._recrawl_btn.configure(state="disabled")
        for name in names:
            self._pending.add(name)
            self._insert_crawling_row(name)
        self._append_log(message)
        threading.Thread(
            target=self._recrawl_all_worker, args=(names,), daemon=True
        ).start()

    def _recrawl_all_worker(self, names: list[str]) -> None:
        """LUÔN gửi "recrawl_done" dù lỗi ở bước nào (kể cả khởi tạo engine thất
        bại) — thiếu bước này sẽ làm `_recrawling`/nút 'Crawl lại tất cả' bị
        kẹt "disabled" vĩnh viễn, user phải khởi động lại app mới bấm được lại
        (còn tệ hơn spam mà nó định chặn)."""

        def log(msg: str) -> None:
            self._msg_queue.put(("log", msg))

        try:
            engine = CrawlerEngine(log=log, use_cache=True)
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI khởi tạo engine crawl lại: {exc}"))
            for name in names:
                self._msg_queue.put(("recrawl_failed", name))
            self._msg_queue.put(("recrawl_done", 0))
            return

        try:
            for name in names:
                items = self._catalog_items.get(name, [])
                try:
                    records = asyncio.run(engine.fetch_live_prices(items))
                except Exception as exc:
                    self._msg_queue.put(("log", f"LỖI crawl lại '{name}': {exc}"))
                    self._msg_queue.put(("recrawl_failed", name))
                    continue
                self._msg_queue.put(("priced", name, records, items))
        finally:
            engine.close()
            self._msg_queue.put(("recrawl_done", len(names)))

    def _site_order(self) -> list[tuple[str, object, str]]:
        """(site_id, SourceName, tên hiển thị) — 9 site cố định theo thứ tự
        CRAWLER_REGISTRY. Không đụng DB (chỉ đọc config) — dùng cả lúc dựng cột
        Treeview (`_build_results_tab`) lẫn làm nền cho `_site_descriptors`."""
        order = []
        for site_id, crawler_cls in CRAWLER_REGISTRY.items():
            cfg = self._sites.get(site_id)
            source = getattr(crawler_cls, "source_name", None)
            if cfg is None or source is None:
                continue
            order.append((site_id, source, cfg.name or site_id))
        return order

    def _site_descriptors(self) -> list[dict]:
        """1 dict/site (đủ cả 9, theo đúng thứ tự cột Treeview) — dùng để tính
        trạng thái từng ô 'Giá theo nguồn', kể cả site không trả về giá lần này."""
        return [
            {"site_id": site_id, "name": name, "source": source}
            for site_id, source, name in self._site_order()
        ]

    def _configure_status_tags(self, tree: ttk.Treeview) -> None:
        """Gắn semantic colors cho Treeview có một trạng thái trên mỗi dòng."""
        for kind, colors in _STATUS_COLORS.items():
            options: dict[str, object] = dict(colors)
            if kind in {"best", "price", "error", "out"}:
                options["font"] = (self._ui_font[0], self._ui_font[1], "bold")
            elif kind == "missing":
                options["font"] = (
                    self._ui_font[0],
                    max(8, self._ui_font[1] - 1),
                    "normal",
                )
            tree.tag_configure(kind, **options)

    def _rebuild_tree_row(self, name: str) -> None:
        """Xóa-và-chèn-lại dòng Treeview cho `name` từ self._selected/_catalog_items
        hiện tại — dùng chung cho mọi chỗ cần vẽ lại 1 dòng sau khi dữ liệu đổi (giá
        mới, lỗi giữ giá cũ, thêm/sửa URL 1 site).

        Chèn lại ĐÚNG VỊ TRÍ cũ (`self._tree.index(name)`) nếu dòng đã tồn tại
        — trước đây luôn chèn ở cuối ("end"), khiến refresh 1 dòng hay crawl
        lại tất cả làm xáo trộn thứ tự thuốc trong danh sách."""
        records = self._selected.get(name, [])
        items = self._catalog_items.get(name, [])
        cells = vm.price_cells_by_source(self._site_descriptors(), items, records)
        index = self._tree.index(name) if self._tree.exists(name) else "end"
        if self._tree.exists(name):
            self._tree.delete(name)
        self._tree.insert(
            "",
            index,
            iid=name,
            values=("", "↻", name, vm.cheapest_label(records), *cells),
        )
        self._renumber_tree()

    def _rekey_selected(self, old_name: str, new_name: str) -> None:
        """Đổi khoá `old_name` → `new_name` trong `self._selected`/
        `self._catalog_items`/`self._tree` — cả 3 đều khoá theo tên chuẩn (xem
        `_restore_selected`/`build_catalog_groups`), nên đổi tên 1 sản phẩm
        ĐANG có trong 'Đã chọn' (xem `_open_edit_listing_dialog`/
        `_confirm_rename_product`) phải đổi khoá theo, không thì dòng cũ bị
        'mồ côi'. Không làm gì nếu tên không đổi hoặc `old_name` không có
        trong 'Đã chọn'."""
        if old_name == new_name:
            return
        if old_name not in self._selected and old_name not in self._catalog_items:
            return
        self._selected[new_name] = self._selected.pop(old_name, [])
        self._catalog_items[new_name] = self._catalog_items.pop(old_name, [])
        if self._tree.exists(old_name):
            self._tree.delete(old_name)

    def _on_priced(self, name: str, records: list, items: list[CatalogItem]) -> None:
        self._pending.discard(name)
        if name in self._cancelled:
            # User đã xóa dòng 'đang crawl' này trước khi worker trả kết quả —
            # đừng hồi sinh dòng đã xóa.
            self._cancelled.discard(name)
            return
        self._selected[name] = records
        self._catalog_items[name] = items
        self._rebuild_tree_row(name)
        self._persist_selected()
        if records:
            self._append_log(f"'{name}': đã có giá live ({len(records)} bản ghi).")
        else:
            self._append_log(
                f"'{name}': không site nào trả giá live — xem các cột theo site để biết vì sao."
            )

    def _on_recrawl_failed(self, name: str) -> None:
        """Lỗi cứng lúc CRAWL LẠI (khác `_on_price_failed` dùng cho lần đầu thêm)
        — KHÔNG xóa khỏi danh sách, chỉ khôi phục hiển thị giá CŨ đã có, tránh
        1 lần lỗi mạng xóa mất dữ liệu user đã tốn công crawl trước đó."""
        self._pending.discard(name)
        if name in self._cancelled:
            self._cancelled.discard(name)
            return
        self._rebuild_tree_row(name)
        self._append_log(f"'{name}': lỗi khi crawl lại — giữ giá cũ.")

    def _on_price_failed(self, name: str) -> None:
        """Worker lỗi cứng (network/exception) trước khi kịp fetch site nào — bỏ
        luôn dòng 'đang crawl' thay vì để nó kẹt vĩnh viễn."""
        self._pending.discard(name)
        if name in self._cancelled:
            self._cancelled.discard(name)
            return
        self._selected.pop(name, None)
        self._catalog_items.pop(name, None)
        if self._tree.exists(name):
            self._tree.delete(name)
        self._renumber_tree()
        self._append_log(f"'{name}': lỗi khi lấy giá live, đã bỏ khỏi danh sách.")

    def _remove_selected(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        names = ", ".join(f"'{iid}'" for iid in sel)
        if not messagebox.askyesno("Xóa dòng", f"Bỏ {names} khỏi 'Đã chọn'?"):
            return
        for iid in sel:
            self._tree.delete(iid)
            self._selected.pop(iid, None)
            if iid in self._pending:
                self._cancelled.add(iid)
            self._catalog_items.pop(iid, None)
        self._renumber_tree()
        self._persist_selected()

    def _clear_all(self) -> None:
        if not self._selected:
            return
        if not messagebox.askyesno(
            "Xóa hết", f"Bỏ toàn bộ {len(self._selected)} sản phẩm khỏi 'Đã chọn'?"
        ):
            return
        for iid in list(self._selected.keys()):
            self._tree.delete(iid)
            if iid in self._pending:
                self._cancelled.add(iid)
        self._selected.clear()
        self._catalog_items.clear()
        self._persist_selected()

    # ------------------------------------------------------------- copy text
    def _copy_listbox_selection(self, _event: tk.Event | None = None) -> str:
        """Ctrl+C copy tên đang chọn trong danh sách gợi ý — Listbox (giống
        Treeview) không tự hỗ trợ Ctrl+C copy nội dung."""
        sel = self._suggestions.curselection()
        if not sel:
            return "break"
        self.clipboard_clear()
        self.clipboard_append("\n".join(self._suggestions.get(i) for i in sel))
        return "break"

    def _bind_tree_copy(self, tree: ttk.Treeview, with_menu: bool = True) -> None:
        """Ctrl+C copy các dòng đang chọn (giá trị mỗi ô nối bằng tab, mỗi dòng
        Treeview 1 dòng text) — Treeview mặc định KHÔNG cho bôi đen/copy nội
        dung ô như Label/Entry thường, user không có cách nào lấy dữ liệu ra
        ngoài trừ xuất file. `with_menu=True` thêm cả menu chuột phải 'Copy'."""

        def _copy(_event: tk.Event | None = None) -> str:
            selected = tree.selection()
            if not selected:
                return "break"
            lines = [
                "\t".join(str(v) for v in tree.item(iid, "values")) for iid in selected
            ]
            self.clipboard_clear()
            self.clipboard_append("\n".join(lines))
            return "break"

        tree.bind("<Control-c>", _copy)
        tree.bind("<Control-C>", _copy)

        if with_menu:
            menu = tk.Menu(tree, tearoff=0)
            menu.add_command(label="Copy dòng đã chọn", command=_copy)

            def _popup(event: tk.Event) -> None:
                iid = tree.identify_row(event.y)
                if iid and iid not in tree.selection():
                    tree.selection_set(iid)
                if tree.selection():
                    menu.tk_popup(event.x_root, event.y_root)

            tree.bind("<Button-3>", _popup)

    def _copyable_text(
        self,
        parent: tk.Widget,
        text: str,
        *,
        font: tuple | None = None,
        height: int = 1,
    ) -> tk.Text:
        """Label 'giả' bằng Text 1-nhiều dòng, chỉ đọc nhưng bôi đen + Ctrl+C
        copy được (ttk.Label KHÔNG hỗ trợ chọn/copy text — đây là lý do người
        dùng không copy được tên thuốc/giá trong bảng chi tiết trước đây)."""
        widget = tk.Text(
            parent,
            height=height,
            wrap="word",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=0,
            pady=0,
            cursor="xterm",
            background=self.cget("background"),
            font=font or self._ui_font,
        )
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        return widget

    def _center_toplevel(self, win: tk.Toplevel, width: int, height: int) -> None:
        """Đặt Toplevel giữa cửa sổ chính — không để hệ điều hành/WM tự chọn vị
        trí (dễ bị đẩy ra góc màn hình, kể cả đã `transient(self)`)."""
        self.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 2)
        win.geometry(f"{width}x{height}+{x}+{y}")

    def _colored_button(
        self, parent: tk.Widget, text: str, command, kind: str
    ) -> tk.Button:
        """Nút màu theo chức năng (`kind`: "danger" đỏ = xóa, "success" xanh lá
        = thêm/lưu, "primary" xanh dương = hành động chính) — dùng `tk.Button`
        cổ điển, KHÔNG phải `ttk.Button` (xem `_BUTTON_COLORS`)."""
        colors = _BUTTON_COLORS[kind]
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=self._ui_font,
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=4,
            cursor="hand2",
            **colors,
        )

    # ------------------------------------------------------------ chi tiết SP
    def _on_tree_right_click(self, event: tk.Event) -> None:
        """Bấm chuột phải vào 1 dòng 'Đã chọn' → chọn dòng đó rồi mở bảng chi
        tiết. Click ra chỗ trống (không trúng dòng nào) thì bỏ qua."""
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        self._tree.selection_set(iid)
        self._show_product_detail(iid)

    def _on_tree_double_click(self, event: tk.Event) -> None:
        """Nhấp đúp 1 dòng 'Đã chọn' → mở bảng chi tiết (giống chuột phải) —
        bảng đó cho sửa/xóa từng site (nhấp đúp 1 dòng site trong đó)."""
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        self._tree.selection_set(iid)
        self._show_product_detail(iid)

    def _on_tree_left_click(self, event: tk.Event) -> None:
        """Bấm cột "↻" ở 1 dòng 'Đã chọn' → crawl lại LIVE riêng dòng đó (xem
        `_refresh_one`) — cột khác thì bỏ qua, để hành vi chọn dòng mặc định
        của Treeview vẫn chạy bình thường (không `return "break"`)."""
        if self._tree.identify_region(event.x, event.y) != "cell":
            return
        refresh_col = f"#{self._tree['columns'].index('refresh') + 1}"
        if self._tree.identify_column(event.x) != refresh_col:
            return
        iid = self._tree.identify_row(event.y)
        if iid:
            self._refresh_one(iid)

    def _refresh_one(self, name: str) -> None:
        """Crawl lại LIVE cho ĐÚNG 1 sản phẩm — nút "↻" riêng từng dòng 'Đã
        chọn', khác `_recrawl_all` (toàn bộ danh sách). Cố tình KHÔNG đụng cờ
        `_recrawling`/nút 'Crawl lại tất cả' — 2 việc phải độc lập, không thì
        xong 1 dòng lại vô tình mở khoá nút 'Crawl lại tất cả' trước khi nó
        thật sự crawl xong hết."""
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        items = self._catalog_items.get(name)
        if items is None:
            return
        if name in self._pending:
            messagebox.showinfo("Đang crawl", f"'{name}' đang lấy giá — chờ xong đã.")
            return
        self._pending.add(name)
        self._insert_crawling_row(name)
        self._append_log(f"Đang crawl lại '{name}'...")
        threading.Thread(
            target=self._refresh_one_worker,
            args=(name, items),
            daemon=True,
        ).start()

    def _refresh_one_worker(self, name: str, items: list[CatalogItem]) -> None:
        """LUÔN gửi kết quả (qua 'priced' hoặc 'recrawl_failed', tái dùng xử lý
        có sẵn ở `_drain_queue`) dù lỗi ở bước nào — cùng lý do các worker
        khác trong file này (tránh dòng bị kẹt 'đang crawl...' vĩnh viễn).
        'recrawl_failed' (không phải 'price_failed') để GIỮ giá cũ khi lỗi,
        đúng tinh thần refresh (không xóa mất dữ liệu đã có)."""

        def log(msg: str) -> None:
            self._msg_queue.put(("log", msg))

        try:
            engine = CrawlerEngine(log=log, use_cache=True)
            try:
                records = asyncio.run(engine.fetch_live_prices(items))
            finally:
                engine.close()
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI crawl lại '{name}': {exc}"))
            self._msg_queue.put(("recrawl_failed", name))
            return
        self._msg_queue.put(("priced", name, records, items))

    def _show_product_detail(
        self,
        name: str,
        items: list[CatalogItem] | None = None,
        records: list | None = None,
    ) -> None:
        """Bảng nhỏ liệt kê chi tiết từng site cho 1 sản phẩm: giá/trạng thái,
        thời gian cập nhật, link sản phẩm — dữ liệu đang crawl dở thì hiện
        luôn trạng thái tạm. Ô tên ở đầu bảng có thể SỬA TRỰC TIẾP (nút "Lưu
        tên" → `_confirm_rename_product`, đổi tên chung mọi site).

        `items`/`records` mặc định lấy từ 'Đã chọn' (`self._catalog_items`/
        `self._selected`, theo `name`) — truyền thẳng khi mở từ bảng TÌM
        thuốc (chưa có trong 'Đã chọn' nên không có 2 dict đó, xem
        `_on_suggestion_right_click`; `records` rỗng vì chưa lấy giá live)."""
        if items is None:
            items = self._catalog_items.get(name, [])
        if records is None:
            records = self._selected.get(name, [])
        rows = vm.product_detail_rows(self._site_descriptors(), items, records)

        # Menu "Sửa" và double-click đều mở bảng chi tiết này. Giữ đúng một
        # instance để click liên tiếp không tạo nhiều màn edit chồng lên nhau.
        current_detail = getattr(self, "_product_detail_window", None)
        if current_detail is not None:
            try:
                if current_detail.winfo_exists():
                    current_detail.lift()
                    current_detail.focus_force()
                    return
            except (tk.TclError, AttributeError):
                pass
            self._product_detail_window = None

        win = tk.Toplevel(self)
        self._product_detail_window = win
        win.title(f"Chi tiết: {name}")
        self._center_toplevel(win, 700, 460)
        win.transient(self)

        name_row = ttk.Frame(win)
        name_row.pack(fill="x", padx=12, pady=(12, 2))
        name_entry = ttk.Entry(name_row, font=("", 12, "bold"))
        name_entry.insert(0, name)
        name_entry.pack(side="left", fill="x", expand=True)
        self._colored_button(
            name_row,
            "Lưu tên",
            lambda: self._confirm_rename_product(name, items, name_entry, win),
            kind="success",
        ).pack(side="left", padx=(6, 0))

        cheapest = vm.cheapest_label(records)
        self._copyable_text(
            win,
            f"Rẻ nhất: {cheapest}" if cheapest else "Chưa có giá nào lấy được.",
        ).pack(anchor="w", fill="x", padx=12, pady=(0, 8))

        detail_frame = ttk.Frame(win)
        detail_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        detail_tree = ttk.Treeview(
            detail_frame,
            columns=("site", "price", "status", "updated", "url"),
            show="headings",
            height=9,
        )
        self._configure_status_tags(detail_tree)
        for col, text, width in (
            ("site", "Site", 140),
            ("price", "Giá", 110),
            ("status", "Trạng thái", 110),
            ("updated", "Cập nhật", 120),
            ("url", "Link", 220),
        ):
            detail_tree.heading(col, text=text)
            detail_tree.column(col, width=width, anchor="w")
        dvbar = ttk.Scrollbar(
            detail_frame, orient="vertical", command=detail_tree.yview
        )
        detail_tree.configure(yscrollcommand=dvbar.set)
        detail_tree.pack(side="left", fill="both", expand=True)
        dvbar.pack(side="right", fill="y")
        self._bind_tree_copy(detail_tree, with_menu=True)

        # iid = site_id (không phải auto-increment) để nhấp đúp biết đúng site nào
        # mà không cần dò ngược từ tên hiển thị (xem `_on_detail_row_double_click`).
        for (site_id, _source, _display), row in zip(self._site_order(), rows):
            detail_tree.insert(
                "",
                "end",
                iid=site_id,
                values=(
                    row["site"],
                    row["price"],
                    row["status"],
                    row["updated"],
                    row["url"],
                ),
                tags=(vm.status_kind(row["status"]),),
            )
        detail_tree.bind(
            "<Double-Button-1>",
            lambda e: self._on_detail_row_double_click(e, detail_tree, name, items),
        )

        ttk.Label(
            win,
            text="Mẹo: nhấp đúp vào 1 dòng để sửa tên/link — trong đó có nút Xóa.",
            foreground="#666666",
        ).pack(anchor="w", padx=12)

        bottom_row = ttk.Frame(win)
        bottom_row.pack(fill="x", padx=12, pady=(6, 12))
        self._colored_button(
            bottom_row,
            "Xóa hoàn toàn sản phẩm",
            lambda: self._confirm_delete_product(name, items, win=win),
            kind="danger",
        ).pack(side="left")
        ttk.Button(bottom_row, text="Đóng", command=win.destroy).pack(side="right")

    def _on_detail_row_double_click(
        self,
        event: tk.Event,
        detail_tree: ttk.Treeview,
        name: str,
        items: list[CatalogItem],
    ) -> None:
        """Nhấp đúp 1 dòng site trong bảng chi tiết → mở dialog Sửa (tên + link,
        có nút Xóa) cho ĐÚNG site đó — xem `_open_edit_listing_dialog`.

        `items` là DANH SÁCH ĐÃ TRUYỀN vào lúc mở bảng chi tiết (xem
        `_show_product_detail`), KHÔNG tự lấy lại từ `self._catalog_items` —
        vì bảng chi tiết còn mở được từ bảng TÌM thuốc (sản phẩm chưa có
        trong 'Đã chọn', tra `self._catalog_items` sẽ ra rỗng)."""
        site_id = detail_tree.identify_row(event.y)
        if not site_id:
            return
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        if self._saving_manual_product:
            messagebox.showinfo("Đang lưu", "Đang lưu thao tác trước đó — chờ xong đã.")
            return

        master_id = items[0].master_product_id if items else ""
        if not master_id:
            messagebox.showinfo(
                "Không hỗ trợ",
                "Sản phẩm này chưa có master_product_id (dữ liệu cũ hoặc chưa gắn "
                "catalog chuẩn) — không sửa/xóa qua đây được.",
            )
            return

        source = getattr(CRAWLER_REGISTRY.get(site_id), "source_name", None)
        if source is None:
            return
        site_names = {sid: display for sid, _s, display in self._site_order()}
        site_display = site_names.get(site_id, site_id)
        current_url = ""
        for it in items:
            if it.source == source:
                current_url = it.source_url
                break

        self._open_edit_listing_dialog(
            name=name,
            master_id=master_id,
            site_id=site_id,
            source=source,
            site_display=site_display,
            current_url=current_url,
        )

    # -------------------------------------------------------- sửa / xóa listing
    def _on_suggestion_right_click(self, event: tk.Event) -> None:
        """Chuột phải vào 1 dòng gợi ý ở bảng tìm thuốc → menu Sửa/Xóa:
        - "Sửa" mở CÙNG bảng chi tiết (đủ mọi site) như bên 'Đã chọn' — nhấp
          đúp 1 dòng site trong đó để sửa tên/link riêng site, xem
          `_show_product_detail`/`_open_edit_listing_dialog`.
        - "Xóa" xóa HOÀN TOÀN sản phẩm khỏi catalog (mọi site cùng lúc), khác
          xóa từng site — xem `_confirm_delete_product`."""
        idx = self._suggestions.nearest(event.y)
        if idx < 0 or idx >= self._suggestions.size():
            return
        self._suggestions.selection_clear(0, "end")
        self._suggestions.selection_set(idx)
        self._suggestions.activate(idx)
        name = self._suggestions.get(idx)
        items = self._groups.get(name, [])
        if not items:
            return

        menu = tk.Menu(self._suggestions, tearoff=0)
        menu.add_command(
            label="Sửa",
            command=lambda: self._show_product_detail(name, items=items, records=[]),
        )
        menu.add_command(
            label="Xóa",
            command=lambda: self._confirm_delete_product(name, items),
        )
        menu.tk_popup(event.x_root, event.y_root)

    def _confirm_delete_product(
        self, name: str, items: list[CatalogItem], win: tk.Toplevel | None = None
    ) -> None:
        """Xóa TOÀN BỘ '{name}' khỏi catalog — mọi site, mọi `master_product_id`
        liên quan (thường 1, có thể >1 nếu nhiều sản phẩm khác nhau bị gộp
        chung 1 tên hiển thị qua alias, xem `gui.viewmodel.build_catalog_groups`
        — gộp hết để không sót site nào của '{name}'), KHÁC `_start_delete_listing`
        (chỉ xóa 1 site). `win`: bảng chi tiết đang mở (nếu gọi từ nút "Xóa
        hoàn toàn sản phẩm" trong đó, xem `_show_product_detail`) — đóng lại
        SAU khi user xác nhận xóa (hủy thì giữ bảng mở)."""
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        if self._saving_manual_product:
            messagebox.showinfo("Đang lưu", "Đang lưu thao tác trước đó — chờ xong đã.")
            return
        master_ids = sorted(
            {it.master_product_id for it in items if it.master_product_id}
        )
        if not master_ids:
            messagebox.showinfo(
                "Không hỗ trợ",
                "Sản phẩm này chưa có master_product_id (dữ liệu cũ hoặc chưa gắn "
                "catalog chuẩn) — không xóa qua đây được.",
                parent=win,
            )
            return
        if not messagebox.askyesno(
            "Xóa sản phẩm",
            f"Xóa TOÀN BỘ '{name}' khỏi catalog (tất cả site đang có)?\n"
            "Không thể hoàn tác.",
            parent=win,
        ):
            return
        if win is not None:
            win.destroy()
        self._start_delete_product(name, master_ids)

    def _start_delete_product(self, name: str, master_ids: list[str]) -> None:
        self._saving_manual_product = True
        self._add_product_btn.configure(state="disabled")
        self._append_log(f"Đang xóa toàn bộ '{name}' khỏi catalog...")
        threading.Thread(
            target=self._delete_product_worker,
            args=(name, master_ids),
            daemon=True,
        ).start()

    def _delete_product_worker(self, name: str, master_ids: list[str]) -> None:
        """LUÔN gửi 'product_delete_done' dù lỗi ở bước nào — cùng lý do các
        worker khác trong file này."""
        ok_any = False
        try:
            for master_id in master_ids:
                if self._engine.remove_product(master_id) is not None:
                    ok_any = True
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI xóa '{name}': {exc}"))
            self._msg_queue.put(("product_delete_done", name, False))
            return
        self._msg_queue.put(("product_delete_done", name, ok_any))

    def _confirm_rename_product(
        self,
        old_name: str,
        items: list[CatalogItem],
        name_entry: ttk.Entry,
        win: tk.Toplevel,
    ) -> None:
        """Đổi tên chuẩn của sản phẩm — nút "Lưu tên" ngay trong bảng chi tiết
        (`_show_product_detail`). Đổi cho MỌI `master_product_id` liên quan
        (thường 1, có thể >1 nếu bị gộp chung tên hiển thị qua alias, cùng lý
        do `_confirm_delete_product`)."""
        new_name = name_entry.get().strip()
        if not new_name or new_name == old_name:
            return
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.", parent=win)
            return
        if self._saving_manual_product:
            messagebox.showinfo(
                "Đang lưu", "Đang lưu thao tác trước đó — chờ xong đã.", parent=win
            )
            return
        master_ids = sorted(
            {it.master_product_id for it in items if it.master_product_id}
        )
        if not master_ids:
            messagebox.showinfo(
                "Không hỗ trợ",
                "Sản phẩm này chưa có master_product_id (dữ liệu cũ hoặc chưa gắn "
                "catalog chuẩn) — không đổi tên qua đây được.",
                parent=win,
            )
            return
        if not messagebox.askyesno(
            "Đổi tên sản phẩm",
            f"Đổi tên '{old_name}' → '{new_name}'?",
            parent=win,
        ):
            return
        win.destroy()
        self._start_rename_product(old_name, new_name, master_ids)

    def _start_rename_product(
        self, old_name: str, new_name: str, master_ids: list[str]
    ) -> None:
        self._saving_manual_product = True
        self._add_product_btn.configure(state="disabled")
        self._append_log(f"Đang đổi tên '{old_name}' → '{new_name}'...")
        threading.Thread(
            target=self._rename_product_worker,
            args=(old_name, new_name, master_ids),
            daemon=True,
        ).start()

    def _rename_product_worker(
        self, old_name: str, new_name: str, master_ids: list[str]
    ) -> None:
        """LUÔN gửi 'product_rename_done' dù lỗi ở bước nào — cùng lý do các
        worker khác trong file này."""
        try:
            for master_id in master_ids:
                self._engine.rename_product(master_id, new_name)
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI đổi tên '{old_name}': {exc}"))
            self._msg_queue.put(("product_rename_done", old_name, new_name, False))
            return
        self._msg_queue.put(("product_rename_done", old_name, new_name, True))

    def _open_edit_listing_dialog(
        self,
        *,
        name: str,
        master_id: str,
        site_id: str,
        source,
        site_display: str,
        current_url: str,
    ) -> None:
        """Dialog Sửa dùng chung cho cả bảng tìm thuốc (chuột phải → Sửa) và
        bảng chi tiết 'Đã chọn' (nhấp đúp 1 dòng site): sửa tên sản phẩm (áp
        dụng chung mọi site — `tên_sản_phẩm_chuẩn`) + link riêng site này, có
        nút Xóa (xóa hẳn listing này khỏi catalog — nếu là site cuối cùng thì
        xóa hẳn cả sản phẩm, xem `utils.catalog_master.delete_listing`)."""
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        if self._saving_manual_product:
            messagebox.showinfo("Đang lưu", "Đang lưu thao tác trước đó — chờ xong đã.")
            return

        # Chỉ cho phép một dialog sửa tồn tại trên toàn ứng dụng. Cả menu chuột
        # phải và double-click ở bảng chi tiết đều đi qua hàm này nên chặn tại
        # đây sẽ bao phủ mọi đường mở dialog.
        current_edit = getattr(self, "_edit_listing_window", None)
        if current_edit is not None:
            try:
                if current_edit.winfo_exists():
                    current_edit.lift()
                    current_edit.focus_force()
                    return
            except (tk.TclError, AttributeError):
                pass
            self._edit_listing_window = None

        win = tk.Toplevel(self)
        self._edit_listing_window = win
        win.title(f"Sửa — {site_display}")
        self._center_toplevel(win, 560, 220)
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=12, pady=12)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Tên sản phẩm:").grid(row=0, column=0, sticky="w", pady=4)
        name_entry = ttk.Entry(body)
        name_entry.insert(0, name)
        name_entry.grid(row=0, column=1, sticky="we", pady=4)
        ttk.Label(body, text=f"Link ({site_display}):").grid(
            row=1, column=0, sticky="w", pady=4
        )
        url_entry = ttk.Entry(body)
        url_entry.insert(0, current_url)
        url_entry.grid(row=1, column=1, sticky="we", pady=4)

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))

        def _do_delete() -> None:
            if not messagebox.askyesno(
                "Xóa listing",
                f"Xóa '{name}' trên {site_display} khỏi catalog?\n"
                "Nếu đây là site cuối cùng, cả sản phẩm sẽ bị xóa hoàn toàn khỏi "
                "catalog. Không thể hoàn tác.",
                parent=win,
            ):
                return
            win.destroy()
            self._start_delete_listing(name, master_id, source, site_display)

        def _do_save() -> None:
            new_name = name_entry.get().strip()
            new_url = url_entry.get().strip()
            if not new_name:
                messagebox.showinfo(
                    "Thiếu tên", "Tên sản phẩm không được để trống.", parent=win
                )
                return
            if not new_url:
                messagebox.showinfo(
                    "Thiếu link", "Link không được để trống.", parent=win
                )
                return
            if new_url != current_url and not detect_product_id(site_id, new_url):
                messagebox.showerror(
                    "Link không hợp lệ",
                    f"Link phải là trang chi tiết sản phẩm thật của {site_display}.\n"
                    "Ứng dụng phải tách được ID sản phẩm từ link trước khi lưu.",
                    parent=win,
                )
                return
            if new_name == name and new_url == current_url:
                win.destroy()
                return
            win.destroy()
            self._start_save_listing(
                name,
                master_id,
                site_id,
                source,
                site_display,
                new_name,
                new_url,
                current_url,
            )

        self._colored_button(btn_row, "Xóa", _do_delete, kind="danger").pack(
            side="left"
        )
        self._colored_button(btn_row, "Lưu", _do_save, kind="success").pack(
            side="right"
        )
        ttk.Button(btn_row, text="Hủy", command=win.destroy).pack(side="right", padx=6)

    def _start_save_listing(
        self,
        old_name: str,
        master_id: str,
        site_id: str,
        source,
        site_display: str,
        new_name: str,
        new_url: str,
        old_url: str,
    ) -> None:
        self._saving_manual_product = True
        self._add_product_btn.configure(state="disabled")
        self._append_log(f"Đang lưu sửa '{old_name}' ({site_display})...")
        threading.Thread(
            target=self._save_listing_edit_worker,
            args=(old_name, master_id, site_id, source, new_name, new_url, old_url),
            daemon=True,
        ).start()

    def _save_listing_edit_worker(
        self,
        old_name: str,
        master_id: str,
        site_id: str,
        source,
        new_name: str,
        new_url: str,
        old_url: str,
    ) -> None:
        """LUÔN gửi 'listing_edit_done' dù lỗi ở bước nào — cùng lý do các
        worker khác trong file này (tránh nút 'Thêm sản phẩm mới' bị kẹt
        'disabled' vĩnh viễn). Đổi tên (`CrawlerEngine.rename_product`, áp dụng
        MỌI site) và đổi link (`CrawlerEngine.set_manual_listing`, CHỈ site
        này) chỉ được thực hiện sau khi URL mới đã xác thực và tách được ID.
        Không tách được ID thì KHÔNG ghi thay đổi nào xuống file."""
        name_changed = new_name != old_name
        url_changed = new_url != old_url
        new_item = None
        if url_changed and not detect_product_id(site_id, new_url):
            self._msg_queue.put(
                (
                    "log",
                    f"'{old_name}': link mới không thuộc đúng trang sản phẩm — "
                    "không lưu tên, link hoặc product_id.",
                )
            )
            self._msg_queue.put(
                (
                    "listing_edit_done",
                    old_name,
                    new_name,
                    master_id,
                    source,
                    None,
                    False,
                    False,
                )
            )
            return
        try:
            if url_changed:
                new_item = self._engine.set_manual_listing(
                    master_id, site_id, new_url, new_name
                )
                if new_item is None:
                    raise ValueError("Không tách được ID sản phẩm từ link mới.")
            if name_changed:
                self._engine.rename_product(master_id, new_name)
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI lưu sửa '{old_name}': {exc}"))
            self._msg_queue.put(
                (
                    "listing_edit_done",
                    old_name,
                    new_name,
                    master_id,
                    source,
                    None,
                    name_changed,
                    False,
                )
            )
            return
        url_ok = url_changed and new_item is not None
        self._msg_queue.put(
            (
                "listing_edit_done",
                old_name,
                new_name,
                master_id,
                source,
                new_item,
                name_changed,
                url_ok,
            )
        )

    def _start_delete_listing(
        self, name: str, master_id: str, source, site_display: str
    ) -> None:
        self._saving_manual_product = True
        self._add_product_btn.configure(state="disabled")
        self._append_log(f"Đang xóa '{name}' ({site_display}) khỏi catalog...")
        threading.Thread(
            target=self._delete_listing_worker,
            args=(name, master_id, source, site_display),
            daemon=True,
        ).start()

    def _delete_listing_worker(
        self, name: str, master_id: str, source, site_display: str
    ) -> None:
        """LUÔN gửi 'listing_delete_done' dù lỗi ở bước nào — cùng lý do các
        worker khác trong file này."""
        try:
            remaining = self._engine.remove_listing(master_id, source)
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI xóa '{name}' ({site_display}): {exc}"))
            self._msg_queue.put(
                ("listing_delete_done", name, master_id, source, False, False)
            )
            return
        ok = remaining is not None
        fully_removed = ok and remaining == 0
        self._msg_queue.put(
            ("listing_delete_done", name, master_id, source, ok, fully_removed)
        )

    # ----------------------------------------------------------------- export
    def _export_excel(self) -> None:
        self._export(".xlsx", [("Excel", "*.xlsx")], "prices.xlsx")

    def _export(self, ext: str, filetypes: list, initialfile: str) -> None:
        if not self._selected:
            messagebox.showinfo(
                "Chưa có dữ liệu", "Hãy thêm thuốc vào danh sách trước khi xuất."
            )
            return
        path = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=initialfile,
        )
        if not path:
            return
        all_records = vm.merge_selected(self._selected)
        try:
            total = writer_for(path).write(all_records)
        except Exception as exc:
            messagebox.showerror("Lỗi ghi file", str(exc))
            return
        messagebox.showinfo(
            "Xuất xong",
            f"Đã ghi {len(all_records)} bản ghi (tổng {total} dòng)\n→ {path}",
        )
        self._append_log(
            f"Xuất {ext}: {len(all_records)} bản ghi → {path} (tổng {total} dòng)."
        )

    # ------------------------------------------------------------- queue pump
    def _drain_queue(self) -> None:
        """Rút hết message đang chờ mỗi 100ms. Lúc log đổ dồn dập (nhiều lần
        "Thêm" cùng lúc), gộp lại thành 1 lượt ghi Text duy nhất mỗi tick thay vì
        1 lần/message — tránh redraw thừa gây nhấp nháy. Log chứa
        AUTH_FAILURE_MARKER (site đăng nhập thất bại) được gộp riêng thành 1
        popup cảnh báo — không thì lỗi đăng nhập chỉ nằm im trong tab Log, dễ bị
        bỏ sót vì không phải tab đang xem."""
        pending_logs: list[str] = []
        auth_failures: list[str] = []
        login_failures: list = []  # LoginCheck thất bại từ startup check
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    pending_logs.append(msg[1])
                    if AUTH_FAILURE_MARKER in msg[1]:
                        auth_failures.append(msg[1])
                elif kind == "priced":
                    self._on_priced(msg[1], msg[2], msg[3])
                elif kind == "price_failed":
                    self._on_price_failed(msg[1])
                elif kind == "recrawl_failed":
                    self._on_recrawl_failed(msg[1])
                elif kind == "recrawl_done":
                    self._recrawling = False
                    self._recrawl_btn.configure(state="normal")
                    pending_logs.append(f"Crawl lại xong: {msg[1]} sản phẩm.")
                elif kind == "manual_product_done":
                    self._saving_manual_product = False
                    self._add_product_btn.configure(state="normal")
                    name, count = msg[1], msg[2]
                    if count:
                        pending_logs.append(
                            f"Đã thêm '{name}' vào catalog ({count} site)."
                        )
                        if self._catalog_ready:
                            self._refresh_suggestions()
                elif kind == "manual_product_duplicate":
                    self._saving_manual_product = False
                    self._add_product_btn.configure(state="normal")
                    messagebox.showerror("Tên bị trùng", msg[2])
                elif kind == "verify_crawl_done":
                    tree, pairs, records = msg[1], msg[2], msg[3]
                    if tree.winfo_exists():
                        by_source = {}
                        for r in records:
                            by_source.setdefault(r.source, r)
                        best = vm.cheapest(records)
                        for site_id, it in pairs:
                            rec = by_source.get(it.source)
                            if (
                                rec is not None
                                and rec.stock_status == StockStatus.OUT_OF_STOCK
                            ):
                                price = (
                                    rec.price_display or f"{rec.price_vnd:,}đ"
                                    if rec.price_vnd > 0
                                    else "—"
                                )
                                status = "Hết hàng"
                            elif rec is not None and rec.price_vnd > 0:
                                price = rec.price_display or f"{rec.price_vnd:,}đ"
                                status = (
                                    "Tốt nhất"
                                    if best is not None and rec is best
                                    else "Có giá"
                                )
                            elif rec is not None:
                                price, status = "—", "Giá ẩn"
                            else:
                                price, status = "—", "Lỗi giá"
                            if tree.exists(site_id):
                                tree.set(site_id, "price", price)
                                tree.set(site_id, "status", status)
                                tree.item(site_id, tags=(vm.status_kind(status),))
                elif kind == "listing_edit_done":
                    self._saving_manual_product = False
                    self._add_product_btn.configure(state="normal")
                    (
                        old_name,
                        new_name,
                        master_id,
                        source,
                        new_item,
                        name_changed,
                        url_ok,
                    ) = msg[1], msg[2], msg[3], msg[4], msg[5], msg[6], msg[7]
                    current_name = old_name
                    if name_changed:
                        self._rekey_selected(old_name, new_name)
                        current_name = new_name
                        for it in self._catalog_items.get(current_name, []):
                            if it.master_product_id == master_id:
                                it.drug_name = new_name
                                it.search_name = strip_accents(new_name).lower()
                    if (
                        url_ok
                        and new_item is not None
                        and current_name in self._catalog_items
                    ):
                        items = self._catalog_items[current_name]
                        items[:] = [it for it in items if it.source != source] + [
                            new_item
                        ]
                    if current_name in self._selected:
                        self._rebuild_tree_row(current_name)
                        self._renumber_tree()
                        self._persist_selected()
                    pending_logs.append(f"'{current_name}': đã lưu thay đổi.")
                    if self._catalog_ready:
                        self._refresh_suggestions()
                elif kind == "product_rename_done":
                    self._saving_manual_product = False
                    self._add_product_btn.configure(state="normal")
                    old_name, new_name, ok = msg[1], msg[2], msg[3]
                    if not ok:
                        pending_logs.append(f"'{old_name}': đổi tên thất bại.")
                    else:
                        self._rekey_selected(old_name, new_name)
                        for it in self._catalog_items.get(new_name, []):
                            it.drug_name = new_name
                            it.search_name = strip_accents(new_name).lower()
                        if new_name in self._selected:
                            self._rebuild_tree_row(new_name)
                            self._renumber_tree()
                            self._persist_selected()
                        pending_logs.append(f"Đã đổi tên '{old_name}' → '{new_name}'.")
                        if self._catalog_ready:
                            self._refresh_suggestions()
                elif kind == "listing_delete_done":
                    self._saving_manual_product = False
                    self._add_product_btn.configure(state="normal")
                    name, master_id, source, ok, fully_removed = (
                        msg[1],
                        msg[2],
                        msg[3],
                        msg[4],
                        msg[5],
                    )
                    if not ok:
                        pending_logs.append(
                            f"'{name}': xóa listing thất bại hoặc không tìm thấy trong catalog."
                        )
                    else:
                        if name in self._catalog_items:
                            items = self._catalog_items[name]
                            items[:] = [it for it in items if it.source != source]
                            if not items or fully_removed:
                                self._selected.pop(name, None)
                                self._catalog_items.pop(name, None)
                                if self._tree.exists(name):
                                    self._tree.delete(name)
                                self._renumber_tree()
                            else:
                                self._rebuild_tree_row(name)
                            self._persist_selected()
                        pending_logs.append(
                            f"'{name}': đã xóa khỏi {source.value}."
                            + (
                                " Sản phẩm không còn site nào — đã xóa hoàn toàn khỏi catalog."
                                if fully_removed
                                else ""
                            )
                        )
                        if self._catalog_ready:
                            self._refresh_suggestions()
                elif kind == "product_delete_done":
                    self._saving_manual_product = False
                    self._add_product_btn.configure(state="normal")
                    name, ok = msg[1], msg[2]
                    if not ok:
                        pending_logs.append(
                            f"'{name}': xóa thất bại hoặc không tìm thấy trong catalog."
                        )
                    else:
                        if name in self._selected or name in self._catalog_items:
                            self._selected.pop(name, None)
                            self._catalog_items.pop(name, None)
                            if self._tree.exists(name):
                                self._tree.delete(name)
                            self._renumber_tree()
                            self._persist_selected()
                        pending_logs.append(f"'{name}': đã xóa hoàn toàn khỏi catalog.")
                        if self._catalog_ready:
                            self._refresh_suggestions()
                elif kind == "catalog_ready":
                    self._catalog_ready = True
                    self._stop_catalog_spinner()
                    stale_names: list[str] = []
                    for name, saved_items in self._catalog_items.items():
                        master_ids = {item.master_product_id for item in saved_items}
                        current_items = [
                            item
                            for item in self._engine.suggest_catalog(name, limit=200)
                            if item.master_product_id in master_ids
                        ]
                        if current_items:
                            reconciled, needs_refresh = vm.reconcile_records_with_items(
                                current_items, self._selected.get(name, [])
                            )
                            self._selected[name] = reconciled
                            self._catalog_items[name] = current_items
                            self._rebuild_tree_row(name)
                            if needs_refresh:
                                stale_names.append(name)
                    if self._catalog_items:
                        self._persist_selected()
                    pending_logs.append(f"Catalog sẵn sàng: {msg[1]} sản phẩm.")
                    if stale_names and not self._recrawling:
                        self._start_recrawl_names(
                            stale_names,
                            "Đang tự cập nhật "
                            f"{len(stale_names)} sản phẩm có dữ liệu giá cũ "
                            "không khớp product ID...",
                        )
                    self._refresh_suggestions()
                elif kind == "catalog_failed":
                    self._stop_catalog_spinner()
                elif kind == "login_check_done":
                    results = msg[1]
                    ok = [r for r in results if r.ok]
                    failed = [r for r in results if not r.ok]
                    pending_logs.append(
                        f"Kiểm tra đăng nhập xong: {len(ok)}/{len(results)} site OK"
                        + (f", {len(failed)} site LỖI." if failed else ".")
                    )
                    login_failures.extend(failed)
        except queue.Empty:
            pass
        if pending_logs:
            self._append_logs(pending_logs)
        if auth_failures:
            self._notify_auth_failures(auth_failures)
        if login_failures:
            self._notify_login_failures(login_failures)
        self.after(100, self._drain_queue)

    def _notify_auth_failures(self, messages: list[str]) -> None:
        """Popup lỗi đăng nhập lúc CRAWL — nhưng mỗi site chỉ popup 1 LẦN/phiên
        (`_auth_warned`). Lần crawl sau site đó vẫn fail thì chỉ ghi Log, không
        bật lại modal (tránh nhá popup liên tục khi recrawl nhiều sản phẩm)."""
        fresh = []
        for m in messages:
            prefix = re.match(r"\[([^\]]+)\]", m)
            key = _auth_key(prefix.group(1) if prefix else m)
            if key in self._auth_warned:
                continue
            self._auth_warned.add(key)
            fresh.append(m)
        if not fresh:
            return
        body = "\n".join(m.replace(AUTH_FAILURE_MARKER + ": ", "") for m in fresh)
        messagebox.showwarning("Đăng nhập thất bại", body)

    def _notify_login_failures(self, failed: list) -> None:
        """1 popup gộp cho kết quả kiểm tra đăng nhập lúc khởi động — liệt kê
        site lỗi + lý do server. Mỗi site chỉ popup 1 lần/phiên (`_auth_warned`),
        chia sẻ với popup lúc crawl nên không báo trùng."""
        fresh = [r for r in failed if _auth_key(r.site_id) not in self._auth_warned]
        if not fresh:
            return
        for r in fresh:
            self._auth_warned.add(_auth_key(r.site_id))
        body = "\n".join(f"• {r.name}: {r.error}" for r in fresh)
        messagebox.showwarning(
            "Đăng nhập thất bại khi khởi động",
            "Các site sau ĐĂNG NHẬP KHÔNG thành công — giá của chúng sẽ bị ẩn (= 0):\n\n"
            f"{body}\n\n"
            "Kiểm tra lại tài khoản/mật khẩu (nút 'Tài khoản' hoặc config/accounts.yaml).",
        )

    def _append_log(self, message: str) -> None:
        self._append_logs([message])

    def _append_logs(self, messages: list[str]) -> None:
        """Ghi nhiều dòng log trong 1 lượt config(normal)/config(disabled) — gọi
        từ `_drain_queue` khi 1 tick có nhiều message dồn lại, tránh redraw
        riêng cho từng dòng. Chỉ tự cuộn xuống cuối nếu đang Ở SẴN cuối log —
        không thì lúc đang crawl (log đổ về liên tục) mà user cuộn lên đọc lại,
        log mới sẽ giật ngược view xuống cuối, đánh nhau với thao tác cuộn
        chuột → nhấp nháy. Đang cuộn lên xem lịch sử thì để yên, không tự nhảy."""
        if not messages:
            return
        ts = datetime.now().strftime("%H:%M:%S")
        at_bottom = self._log.yview()[1] >= 0.999
        self._log.config(state="normal")
        for message in messages:
            self._log.insert("end", f"[{ts}] {message}\n")
        if at_bottom:
            self._log.see("end")
        self._log.config(state="disabled")

    # --------------------------------------------------- credentials editor
    def _open_credentials_editor(self) -> None:
        """Popup sửa username/password từng site → ghi thẳng vào accounts.yaml.

        Ví von: như bảng "đổi mật khẩu" — mỗi site 1 dòng, sửa xong bấm Lưu.
        Tài khoản mới áp dụng cho lần crawl kế tiếp (worker luôn tạo engine mới,
        đọc lại config), nên không cần khởi động lại app.
        """
        try:
            sites = load_sites()
        except Exception as exc:
            messagebox.showwarning(
                "Thiếu cấu hình",
                f"Không đọc được config/accounts.yaml:\n{exc}\n\n"
                "Copy config/accounts.example.yaml → config/accounts.yaml trước.",
            )
            return
        if not sites:
            messagebox.showinfo("Chưa có site", "accounts.yaml không có site nào.")
            return

        win = tk.Toplevel(self)
        win.title("Sửa tài khoản đăng nhập")
        self._center_toplevel(win, 560, 560)
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win,
            text="Sửa username/password rồi bấm Lưu. Lưu vào config/accounts.yaml.",
            wraplength=520,
        ).pack(anchor="w", padx=12, pady=(10, 4))

        # Vùng cuộn (9 site có thể dài hơn cửa sổ).
        outer = ttk.Frame(win)
        outer.pack(fill="both", expand=True, padx=12, pady=4)
        canvas = tk.Canvas(outer, highlightthickness=0)
        vbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        body = ttk.Frame(canvas)
        body.bind(
            "<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=body, anchor="nw")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        # Kéo thanh cuộn bằng chuột hoạt động, nhưng lăn con lăn chuột thì không —
        # Canvas không tự nhận sự kiện lăn chuột của các widget con bên trong (Entry,
        # Label...). Bind_all CHỈ khi chuột đang ở trong vùng canvas (Enter/Leave)
        # để không ảnh hưởng cửa sổ chính. `<Button-4>`/`<Button-5>` cho Linux/X11,
        # `<MouseWheel>` cho Windows/Mac.
        def _on_mousewheel(event: tk.Event) -> None:
            delta = -1 if getattr(event, "num", None) == 4 or event.delta > 0 else 1
            canvas.yview_scroll(delta, "units")

        def _bind_wheel(_e: tk.Event) -> None:
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_mousewheel)
            canvas.bind_all("<Button-5>", _on_mousewheel)

        def _unbind_wheel(_e: tk.Event) -> None:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

        entries: dict[str, tuple[ttk.Entry, ttk.Entry, str, str]] = {}
        for site_id, cfg in sites.items():
            box = ttk.LabelFrame(body, text=f"{cfg.name or site_id}  ({site_id})")
            box.pack(fill="x", expand=True, padx=4, pady=5)

            ttk.Label(box, text="Username:").grid(
                row=0, column=0, sticky="w", padx=6, pady=3
            )
            u_entry = ttk.Entry(box, width=42)
            u_entry.insert(0, cfg.credentials.username)
            u_entry.grid(row=0, column=1, sticky="we", padx=6, pady=3)

            ttk.Label(box, text="Password:").grid(
                row=1, column=0, sticky="w", padx=6, pady=3
            )
            p_entry = ttk.Entry(box, width=42, show="•")
            p_entry.insert(0, cfg.credentials.password)
            p_entry.grid(row=1, column=1, sticky="we", padx=6, pady=3)

            show_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                box,
                text="Hiện",
                variable=show_var,
                command=lambda e=p_entry, v=show_var: e.config(
                    show="" if v.get() else "•"
                ),
            ).grid(row=1, column=2, padx=6)

            box.columnconfigure(1, weight=1)
            entries[site_id] = (
                u_entry,
                p_entry,
                cfg.credentials.username,
                cfg.credentials.password,
            )

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(4, 12))
        self._colored_button(
            btn_row,
            "Lưu",
            lambda: self._save_credentials(entries, win),
            kind="success",
        ).pack(side="right")
        ttk.Button(btn_row, text="Hủy", command=win.destroy).pack(side="right", padx=6)

    def _save_credentials(
        self,
        entries: dict[str, tuple[ttk.Entry, ttk.Entry, str, str]],
        win: tk.Toplevel,
    ) -> None:
        """Ghi các site có thay đổi; bỏ qua site giữ nguyên để không đụng file thừa."""
        saved = 0
        errors: list[str] = []
        for site_id, (u_entry, p_entry, orig_u, orig_p) in entries.items():
            username, password = u_entry.get(), p_entry.get()
            if username == orig_u and password == orig_p:
                continue
            try:
                update_credentials(site_id, username, password)
                saved += 1
                # Đổi tài khoản thì phiên đăng nhập cache (crawlers/base.py)
                # của tài khoản CŨ phải bỏ — không thì lần fetch kế tiếp có
                # thể vô tình dùng lại session của tài khoản vừa đổi.
                source = getattr(CRAWLER_REGISTRY.get(site_id), "source_name", None)
                if source is not None:
                    clear_auth_cache(source)
            except Exception as exc:
                errors.append(f"{site_id}: {exc}")

        if errors:
            messagebox.showerror("Lỗi lưu", "\n".join(errors))
            return
        if saved == 0:
            messagebox.showinfo("Không có thay đổi", "Không có tài khoản nào thay đổi.")
            win.destroy()
            return

        self._sites = self._safe_load_sites()
        self._append_log(f"Đã lưu tài khoản {saved} site vào config/accounts.yaml.")
        messagebox.showinfo(
            "Đã lưu",
            f"Đã cập nhật {saved} site.\nTài khoản mới áp dụng cho lần crawl kế tiếp.",
        )
        win.destroy()

    def destroy(self) -> None:
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception:
                pass
        super().destroy()

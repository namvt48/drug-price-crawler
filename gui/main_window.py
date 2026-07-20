"""MainWindow — GUI tkinter cho PharmaPrice.

Ví von: tkinter là "quầy lễ tân" chỉ nói được một luồng (UI thread), còn
crawl là "việc bếp núc" chạy asyncio. Không thể để lễ tân xuống bếp (block UI),
sẽ crawl chạy ở thread riêng và gửi tin nhắn về qua một khay (queue.Queue);
lễ tân định kỳ ra lấy khay (after 100ms) để cập nhật log/progress.

Luồng mới:
- Gõ tên thuốc → gợi ý từ catalog_master_entity_resolved.xlsx (nạp tĩnh 1 lần
  trong thread nền lúc khởi động, xem `_start_catalog_warmup`/
  `utils.catalog_master.load_master_catalog` — không còn tự crawl/refresh catalog
  từng site nữa; gõ tìm trước khi nạp xong sẽ không có gợi ý cho tới khi xong).
- Chọn thuốc → đưa vào danh sách đã chọn (mỗi thuốc 1 dòng, gộp giá nhiều nguồn).
- Danh sách đã chọn tự lưu ra output/selected_products.json sau mỗi lần
  thêm/xóa (xem `utils.selected_store`) — mở lại app hiện ngay bằng giá đã lưu,
  KHÔNG tự fetch live lại (tránh dội request vào nhiều site cùng lúc).
- "Xuất CSV" ghi toàn bộ bản ghi đã chọn ra file.
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
from crawlers.engine import CrawlerEngine
from gui import viewmodel as vm
from utils.config_loader import load_sites, update_credentials
from utils.excel_writer import writer_for
from utils.models import CatalogItem
from utils.normalizer import strip_accents
from utils.selected_store import load_selected, save_selected
from utils.url_detect import detect_product_id, suggest_name_from_urls

DEFAULT_CSV = "output/prices.csv"


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


class MainWindow(tk.Tk):
    # Ứng viên font theo thứ tự ưu tiên — "Segoe UI" (Windows chuẩn) trước,
    # sau đó các font Vietnamese-capable phổ biến trên Linux. Chọn font ĐẦU
    # TIÊN thật sự có trên máy (xem `_configure_fonts`).
    _FONT_CANDIDATES = ("Segoe UI", "Noto Sans", "Ubuntu", "DejaVu Sans", "Arial", "Helvetica")

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
        self._groups: dict[str, list[CatalogItem]] = {}  # canonical_name -> [catalog item]
        self._catalog_items: dict[str, list[CatalogItem]] = {}  # canonical_name -> catalog items dùng để fetch
        self._pending: set[str] = set()  # tên đang chờ fetch giá live, chặn thêm trùng khi worker chưa xong
        self._cancelled: set[str] = set()  # tên bị xóa thủ công lúc còn đang crawl — bỏ qua kết quả trễ
        self._suggest_job: str | None = None  # id `after()` debounce gợi ý, hủy job cũ khi gõ tiếp
        self._catalog_ready = False  # True sau khi _warm_catalog_worker nạp xong catalog_master_entity_resolved.xlsx
        self._recrawling = False  # True trong lúc _recrawl_all_worker đang chạy, chặn bấm chồng
        self._saving_manual_product = False  # True trong lúc _save_manual_product_worker đang ghi xlsx
        self._auth_warned: set[str] = set()  # site đã popup lỗi đăng nhập rồi — không popup lại (chỉ log)
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
            "SunValleyCaptionFont", "SunValleyBodyFont",
            "TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont",
        ):
            try:
                f = tkfont.nametofont(name)
            except tk.TclError:
                continue
            f.configure(family=family or f.actual("family"), size=base_size)

        self._ui_font = (family or tkfont.nametofont("TkDefaultFont").actual("family"), base_size)

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
        ttk.Label(title_row, text="PharmaPrice", font=("", 13, "bold")).pack(side="left")
        self._credentials_btn = ttk.Button(
            title_row, text="⚙ Sửa tài khoản", command=self._open_credentials_editor
        )
        self._credentials_btn.pack(side="right")

        # 2 tab riêng: Kết quả (search+chọn+export) tách khỏi Log, để khỏi
        # phải cuộn qua nửa màn hình mới thấy log.
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        results_tab = ttk.Frame(notebook)
        log_tab = ttk.Frame(notebook)
        notebook.add(results_tab, text="🔍 Tìm & Kết quả")
        notebook.add(log_tab, text="📋 Log")

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
        self._search.bind("<KeyRelease>", lambda _e: self._schedule_refresh_suggestions())
        # Spinner khi catalog chưa nạp xong — dùng ttk.Progressbar (vẽ bằng theme,
        # KHÔNG phải ký tự Unicode) vì spinner kiểu chữ (vd Braille "⠋⠙⠹...") có
        # thể không hiện được nếu font đang chọn thiếu glyph đó (tuỳ máy) — im
        # lặng thành ô trống, user tưởng app treo. Progressbar luôn vẽ được, không
        # phụ thuộc font. Ẩn hẳn (không chỉ để rỗng) khi catalog đã sẵn sàng.
        self._catalog_spinner_label = ttk.Label(search_row, text="Đang tải catalog...")
        self._catalog_progress = ttk.Progressbar(search_row, mode="indeterminate", length=90)

        sug_frame = ttk.Frame(parent)
        sug_frame.pack(fill="x", padx=10)
        # activestyle="none": Listbox là widget Tk cổ điển (không qua ttk theme),
        # mặc định gạch chân/tô item đang hover → redraw thừa mỗi khi rê chuột,
        # dễ nhấp nháy trên X server không có compositor.
        self._suggestions = tk.Listbox(
            sug_frame, height=8, activestyle="none", exportselection=False,
            font=self._ui_font, **_CLASSIC_WIDGET_COLORS,
        )
        sug_scroll = ttk.Scrollbar(sug_frame, command=self._suggestions.yview)
        self._suggestions.configure(yscrollcommand=sug_scroll.set)
        self._suggestions.pack(side="left", fill="both", expand=True)
        sug_scroll.pack(side="right", fill="y")
        self._suggestions.bind("<Double-Button-1>", lambda _e: self._add_selected())
        self._suggestions.bind("<Control-c>", self._copy_listbox_selection)
        self._suggestions.bind("<Control-C>", self._copy_listbox_selection)

        add_row = ttk.Frame(parent)
        add_row.pack(fill="x", padx=10)
        self._add_btn = ttk.Button(add_row, text="➕ Thêm", command=self._add_selected)
        self._add_btn.pack(side="left")
        self._add_product_btn = ttk.Button(
            add_row, text="🆕 Thêm sản phẩm mới", command=self._open_add_product_dialog
        )
        self._add_product_btn.pack(side="left", padx=6)

        # --- Selected list ---
        # Cột "Giá theo nguồn" trước đây gộp cả 9 site vào 1 chuỗi dài — giờ
        # tách hẳn 9 cột riêng (1 cột/site, header = tên site) cho dễ so sánh,
        # đổi lại bảng rộng hơn cửa sổ nên cần thanh cuộn ngang. Có thêm cột STT
        # (đánh lại mỗi khi thêm/xóa dòng, xem `_renumber_tree`) và bấm chuột
        # phải vào 1 dòng để xem chi tiết (xem `_on_tree_right_click`).
        ttk.Label(parent, text="Đã chọn:", font=("", 10, "bold")).pack(anchor="w", padx=10, pady=(6, 0))
        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=2)
        site_order = self._site_order()
        site_ids = tuple(sid for sid, _, _ in site_order)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("stt", "name", "cheapest") + site_ids,
            show="headings",
            height=10,
        )
        self._tree.heading("stt", text="STT")
        self._tree.heading("name", text="Tên thuốc")
        self._tree.heading("cheapest", text="Rẻ nhất ★")
        self._tree.column("stt", width=40, anchor="center", stretch=False)
        self._tree.column("name", width=170, anchor="w")
        self._tree.column("cheapest", width=130, anchor="w")
        for sid, _source, display_name in site_order:
            self._tree.heading(sid, text=display_name)
            self._tree.column(sid, width=90, anchor="center")
        self._tree.bind("<Button-3>", self._on_tree_right_click)
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
        self._remove_btn = ttk.Button(sel_row, text="🗑 Xóa dòng", command=self._remove_selected)
        self._remove_btn.pack(side="left")
        self._clear_btn = ttk.Button(sel_row, text="Xóa hết", command=self._clear_all)
        self._clear_btn.pack(side="left", padx=6)
        self._recrawl_btn = ttk.Button(
            sel_row, text="🔄 Crawl lại tất cả", command=self._recrawl_all
        )
        self._recrawl_btn.pack(side="left", padx=6)
        self._export_csv_btn = ttk.Button(sel_row, text="💾 Xuất CSV", command=self._export_csv)
        self._export_csv_btn.pack(side="right")
        self._export_excel_btn = ttk.Button(sel_row, text="📊 Xuất Excel", command=self._export_excel)
        self._export_excel_btn.pack(side="right", padx=6)

    def _build_log_tab(self, parent: ttk.Frame) -> None:
        btn_row = ttk.Frame(parent)
        btn_row.pack(fill="x", padx=10, pady=(10, 0))
        ttk.Button(btn_row, text="📋 Copy toàn bộ log", command=self._copy_log).pack(side="left")

        log_frame = ttk.Frame(parent)
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self._log = tk.Text(
            log_frame, wrap="word", state="disabled", font=self._ui_font, **_CLASSIC_WIDGET_COLORS
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
        """Nạp catalog_master_entity_resolved.xlsx (~58k dòng, vài chục giây) trong
        thread nền ngay lúc mở app, để lúc user gõ tìm lần đầu không bị đứng UI chờ
        đọc file. `_refresh_suggestions` tự bỏ qua cho tới khi có tin "catalog_ready"."""
        if self._engine is None:
            return
        self._append_log("Đang tải catalog sản phẩm chuẩn (có thể mất khoảng 1 phút)...")
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
            self._msg_queue.put(("log", m.replace(AUTH_FAILURE_MARKER + ": ", "").replace(AUTH_FAILURE_MARKER, "⚠")))

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
        """Gợi ý theo tên chuẩn, dựa trên catalog_master_entity_resolved.xlsx (tên+SKU
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
        khi restore nhiều sản phẩm cùng lúc). Muốn giá mới thì xóa dòng rồi thêm
        lại (tự động fetch live như bình thường)."""
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
                "", "end", iid=name,
                values=("", name, vm.cheapest_label(records), *cells),
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
            messagebox.showinfo("Không có dữ liệu", f"Catalog không có sản phẩm cho '{name}'.")
            return
        self._start_add(name, items)

    # ------------------------------------------------------ thêm sản phẩm mới
    def _open_add_product_dialog(self) -> None:
        """Dialog thêm 1 sản phẩm CHƯA CÓ trong catalog: dán URL cho tối đa 9 site
        (bỏ trống site không bán) → 'Xác nhận' tách product_id CƠ HỌC từ URL (xem
        `utils.url_detect`, không gọi mạng nên tức thời) → bước 2 xác nhận/sửa tên
        → 'Lưu' ghi vào catalog_master_entity_resolved.xlsx (xem
        `CrawlerEngine.add_manual_product`)."""
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        if self._saving_manual_product:
            messagebox.showinfo("Đang lưu", "Đang lưu sản phẩm trước đó — chờ xong đã.")
            return

        win = tk.Toplevel(self)
        win.title("Thêm sản phẩm mới")
        win.geometry("640x480")
        win.transient(self)
        win.grab_set()

        ttk.Label(
            win,
            text="Dán URL trang chi tiết sản phẩm cho các site có bán (bỏ trống site không có):",
            wraplength=600, justify="left",
        ).pack(anchor="w", padx=12, pady=(10, 6))

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=12)
        entries: dict[str, ttk.Entry] = {}
        for row_i, (site_id, _source, name) in enumerate(self._site_order()):
            ttk.Label(body, text=f"{name}:").grid(row=row_i, column=0, sticky="w", padx=(0, 6), pady=3)
            entry = ttk.Entry(body, width=60)
            entry.grid(row=row_i, column=1, sticky="we", pady=3)
            entries[site_id] = entry
        body.columnconfigure(1, weight=1)

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(8, 12))
        ttk.Button(
            btn_row, text="Xác nhận", command=lambda: self._confirm_product_urls(entries, win)
        ).pack(side="right")
        ttk.Button(btn_row, text="Hủy", command=win.destroy).pack(side="right", padx=6)

    def _confirm_product_urls(self, entries: dict[str, ttk.Entry], win: tk.Toplevel) -> None:
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

        site_names = {site_id: name for site_id, _source, name in self._site_order()}
        for widget in win.winfo_children():
            widget.destroy()

        ttk.Label(
            win, text=f"Đã nhận diện {len(detected)}/{len(urls)} site có dán URL:",
            font=("", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 4))
        detected_text = ", ".join(site_names.get(sid, sid) for sid in detected)
        self._copyable_text(win, detected_text, height=2).pack(
            anchor="w", fill="x", padx=12, pady=(0, 10)
        )

        ttk.Label(win, text="Tên sản phẩm (gợi ý từ URL — sửa lại cho đúng):").pack(
            anchor="w", padx=12
        )
        name_entry = ttk.Entry(win)
        name_entry.insert(0, suggest_name_from_urls(list(urls.values())))
        name_entry.pack(fill="x", padx=12, pady=(2, 12))

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(
            btn_row, text="💾 Lưu",
            command=lambda: self._save_manual_product(name_entry.get().strip(), detected, win),
        ).pack(side="right")
        ttk.Button(btn_row, text="Hủy", command=win.destroy).pack(side="right", padx=6)

    def _save_manual_product(self, name: str, detected: dict[str, str], win: tk.Toplevel) -> None:
        if not name:
            messagebox.showinfo("Thiếu tên", "Nhập tên sản phẩm trước khi lưu.")
            return
        win.destroy()
        self._saving_manual_product = True
        self._add_product_btn.configure(state="disabled")
        self._append_log(
            f"Đang lưu sản phẩm mới '{name}' vào catalog (ghi file ~40k dòng, có thể mất 2-3 phút)..."
        )
        threading.Thread(
            target=self._save_manual_product_worker, args=(name, detected), daemon=True,
        ).start()

    def _save_manual_product_worker(self, name: str, urls: dict[str, str]) -> None:
        """LUÔN gửi 'manual_product_done' dù lỗi ở bước nào — thiếu bước này sẽ làm
        nút '🆕 Thêm sản phẩm mới' kẹt 'disabled' vĩnh viễn, giống bài học đã sửa ở
        `_recrawl_all_worker`."""
        try:
            items = self._engine.add_manual_product(urls, name)
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
            messagebox.showinfo("Trùng", f"'{name}' đã có trong danh sách (hoặc đang lấy giá).")
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
        """Dòng tạm 'đang crawl' cho đủ số cột (STT + Tên thuốc + Rẻ nhất + 9
        site) — `_on_priced`/`_on_price_failed` sẽ xóa-và-chèn-lại đúng iid này
        khi có kết quả thật (xem `_tree.exists`/`_tree.delete` ở đó). STT để
        rỗng, `_renumber_tree()` đánh số lại ngay sau."""
        site_count = len(self._site_order())
        if self._tree.exists(name):
            self._tree.delete(name)
        self._tree.insert(
            "", "end", iid=name,
            values=("", name, "⏳ đang crawl...", *(("⏳ đang crawl...",) * site_count)),
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
            messagebox.showinfo("Đang crawl", "Đang crawl lại — chờ xong đã rồi bấm tiếp.")
            return
        names = [n for n in self._selected if n not in self._pending]
        if not names:
            messagebox.showinfo("Danh sách trống", "Chưa có sản phẩm nào trong 'Đã chọn'.")
            return
        self._recrawling = True
        self._recrawl_btn.configure(state="disabled")
        for name in names:
            self._pending.add(name)
            self._insert_crawling_row(name)
        self._append_log(f"Đang crawl lại giá cho {len(names)} sản phẩm (tuần tự, tránh rate-limit)...")
        threading.Thread(target=self._recrawl_all_worker, args=(names,), daemon=True).start()

    def _recrawl_all_worker(self, names: list[str]) -> None:
        """LUÔN gửi "recrawl_done" dù lỗi ở bước nào (kể cả khởi tạo engine thất
        bại) — thiếu bước này sẽ làm `_recrawling`/nút '🔄 Crawl lại tất cả' bị
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

    def _rebuild_tree_row(self, name: str) -> None:
        """Xóa-và-chèn-lại dòng Treeview cho `name` từ self._selected/_catalog_items
        hiện tại — dùng chung cho mọi chỗ cần vẽ lại 1 dòng sau khi dữ liệu đổi (giá
        mới, lỗi giữ giá cũ, thêm/sửa URL 1 site)."""
        records = self._selected.get(name, [])
        items = self._catalog_items.get(name, [])
        cells = vm.price_cells_by_source(self._site_descriptors(), items, records)
        if self._tree.exists(name):
            self._tree.delete(name)
        self._tree.insert(
            "", "end", iid=name,
            values=("", name, vm.cheapest_label(records), *cells),
        )
        self._renumber_tree()

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
            self._append_log(f"'{name}': không site nào trả giá live — xem các cột theo site để biết vì sao.")

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
        for iid in sel:
            self._tree.delete(iid)
            self._selected.pop(iid, None)
            if iid in self._pending:
                self._cancelled.add(iid)
            self._catalog_items.pop(iid, None)
        self._renumber_tree()
        self._persist_selected()

    def _clear_all(self) -> None:
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
            lines = ["\t".join(str(v) for v in tree.item(iid, "values")) for iid in selected]
            self.clipboard_clear()
            self.clipboard_append("\n".join(lines))
            return "break"

        tree.bind("<Control-c>", _copy)
        tree.bind("<Control-C>", _copy)

        if with_menu:
            menu = tk.Menu(tree, tearoff=0)
            menu.add_command(label="📋 Copy dòng đã chọn", command=_copy)

            def _popup(event: tk.Event) -> None:
                iid = tree.identify_row(event.y)
                if iid and iid not in tree.selection():
                    tree.selection_set(iid)
                if tree.selection():
                    menu.tk_popup(event.x_root, event.y_root)

            tree.bind("<Button-3>", _popup)

    def _copyable_text(
        self, parent: tk.Widget, text: str, *, font: tuple | None = None, height: int = 1
    ) -> tk.Text:
        """Label 'giả' bằng Text 1-nhiều dòng, chỉ đọc nhưng bôi đen + Ctrl+C
        copy được (ttk.Label KHÔNG hỗ trợ chọn/copy text — đây là lý do người
        dùng không copy được tên thuốc/giá trong bảng chi tiết trước đây)."""
        widget = tk.Text(
            parent, height=height, wrap="word", relief="flat", borderwidth=0,
            highlightthickness=0, padx=0, pady=0, cursor="xterm",
            background=self.cget("background"), font=font or self._ui_font,
        )
        widget.insert("1.0", text)
        widget.configure(state="disabled")
        return widget

    # ------------------------------------------------------------ chi tiết SP
    def _on_tree_right_click(self, event: tk.Event) -> None:
        """Bấm chuột phải vào 1 dòng 'Đã chọn' → chọn dòng đó rồi mở bảng chi
        tiết. Click ra chỗ trống (không trúng dòng nào) thì bỏ qua."""
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        self._tree.selection_set(iid)
        self._show_product_detail(iid)

    def _show_product_detail(self, name: str) -> None:
        """Bảng nhỏ liệt kê chi tiết từng site cho 1 sản phẩm đã chọn: giá
        (kèm trạng thái như cột Treeview), nhà sản xuất, thời gian cập nhật,
        link sản phẩm — dữ liệu đang crawl dở thì hiện luôn trạng thái tạm."""
        records = self._selected.get(name, [])
        items = self._catalog_items.get(name, [])
        rows = vm.product_detail_rows(self._site_descriptors(), items, records)

        win = tk.Toplevel(self)
        win.title(f"Chi tiết: {name}")
        win.geometry("700x420")
        win.transient(self)

        self._copyable_text(win, name, font=("", 12, "bold"), height=2).pack(
            anchor="w", fill="x", padx=12, pady=(12, 2)
        )
        cheapest = vm.cheapest_label(records)
        self._copyable_text(
            win, f"Rẻ nhất: {cheapest}" if cheapest else "Chưa có giá nào lấy được.",
        ).pack(anchor="w", fill="x", padx=12, pady=(0, 8))

        detail_frame = ttk.Frame(win)
        detail_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        detail_tree = ttk.Treeview(
            detail_frame,
            columns=("site", "status", "manufacturer", "updated", "url"),
            show="headings",
            height=9,
        )
        for col, text, width in (
            ("site", "Site", 140),
            ("status", "Giá / trạng thái", 130),
            ("manufacturer", "Nhà SX", 130),
            ("updated", "Cập nhật", 120),
            ("url", "Link", 220),
        ):
            detail_tree.heading(col, text=text)
            detail_tree.column(col, width=width, anchor="w")
        dvbar = ttk.Scrollbar(detail_frame, orient="vertical", command=detail_tree.yview)
        detail_tree.configure(yscrollcommand=dvbar.set)
        detail_tree.pack(side="left", fill="both", expand=True)
        dvbar.pack(side="right", fill="y")
        self._bind_tree_copy(detail_tree, with_menu=True)

        # iid = site_id (không phải auto-increment) để nhấp đúp biết đúng site nào
        # mà không cần dò ngược từ tên hiển thị (xem `_on_detail_row_double_click`).
        for (site_id, _source, _display), row in zip(self._site_order(), rows):
            detail_tree.insert(
                "", "end", iid=site_id,
                values=(row["site"], row["status"], row["manufacturer"], row["updated"], row["url"]),
            )
        detail_tree.bind(
            "<Double-Button-1>",
            lambda e: self._on_detail_row_double_click(e, detail_tree, name),
        )

        ttk.Label(
            win, text="Mẹo: nhấp đúp vào 1 dòng để dán/sửa URL sản phẩm cho site đó.",
            foreground="#666666",
        ).pack(anchor="w", padx=12)
        ttk.Button(win, text="Đóng", command=win.destroy).pack(pady=(6, 12))

    def _on_detail_row_double_click(
        self, event: tk.Event, detail_tree: ttk.Treeview, name: str
    ) -> None:
        """Nhấp đúp 1 dòng site trong bảng chi tiết → hỏi URL (dán mới hoặc sửa URL
        đã có) → tách product_id (utils.url_detect, không gọi mạng) → ghi thêm/sửa
        dòng source_listings gắn vào ĐÚNG master_product_id của sản phẩm này (xem
        `CrawlerEngine.set_manual_listing`) — không tạo sản phẩm mới, chỉ bổ sung
        site cho sản phẩm ĐÃ CÓ."""
        site_id = detail_tree.identify_row(event.y)
        if not site_id:
            return
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        if self._saving_manual_product:
            messagebox.showinfo("Đang lưu", "Đang lưu thao tác trước đó — chờ xong đã.")
            return

        items = self._catalog_items.get(name, [])
        master_id = items[0].master_product_id if items else ""
        if not master_id:
            messagebox.showinfo(
                "Không hỗ trợ",
                "Sản phẩm này chưa có master_product_id (dữ liệu cũ hoặc chưa gắn "
                "catalog chuẩn) — không thêm/sửa URL qua đây được.",
            )
            return

        site_names = {sid: display for sid, _s, display in self._site_order()}
        site_display = site_names.get(site_id, site_id)
        current_url = ""
        for it in items:
            source = getattr(CRAWLER_REGISTRY.get(site_id), "source_name", None)
            if source is not None and it.source == source:
                current_url = it.source_url
                break

        url = simpledialog.askstring(
            f"URL — {site_display}",
            f"Dán URL trang chi tiết sản phẩm '{name}' trên {site_display}:",
            initialvalue=current_url,
            parent=detail_tree.winfo_toplevel(),
        )
        if url is None:
            return
        url = url.strip()
        if not url or url == current_url:
            return

        self._saving_manual_product = True
        self._add_product_btn.configure(state="disabled")
        self._append_log(
            f"Đang lưu URL {site_display} cho '{name}' (ghi file catalog, có thể mất 2-3 phút)..."
        )
        threading.Thread(
            target=self._save_listing_url_worker,
            args=(name, site_id, url, master_id),
            daemon=True,
        ).start()

    def _save_listing_url_worker(self, name: str, site_id: str, url: str, master_id: str) -> None:
        """LUÔN gửi 'listing_url_done' dù lỗi ở bước nào — cùng lý do đã sửa ở
        `_recrawl_all_worker`/`_save_manual_product_worker` (tránh nút bị kẹt
        'disabled' vĩnh viễn)."""
        try:
            new_item = self._engine.set_manual_listing(master_id, site_id, url, name)
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI lưu URL cho '{name}': {exc}"))
            self._msg_queue.put(("listing_url_done", name, None))
            return
        self._msg_queue.put(("listing_url_done", name, new_item))

    # ----------------------------------------------------------------- export
    def _export_csv(self) -> None:
        self._export(".csv", [("CSV", "*.csv")], "prices.csv")

    def _export_excel(self) -> None:
        self._export(".xlsx", [("Excel", "*.xlsx")], "prices.xlsx")

    def _export(self, ext: str, filetypes: list, initialfile: str) -> None:
        if not self._selected:
            messagebox.showinfo("Chưa có dữ liệu", "Hãy thêm thuốc vào danh sách trước khi xuất.")
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
        self._append_log(f"Xuất {ext}: {len(all_records)} bản ghi → {path} (tổng {total} dòng).")

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
                        pending_logs.append(f"Đã thêm '{name}' vào catalog ({count} site).")
                        if self._catalog_ready:
                            self._refresh_suggestions()
                elif kind == "listing_url_done":
                    self._saving_manual_product = False
                    self._add_product_btn.configure(state="normal")
                    name, new_item = msg[1], msg[2]
                    if new_item is not None:
                        items = self._catalog_items.setdefault(name, [])
                        items[:] = [it for it in items if it.source != new_item.source] + [new_item]
                        self._rebuild_tree_row(name)
                        self._persist_selected()
                        pending_logs.append(f"'{name}': đã lưu URL {new_item.source.value}.")
                    else:
                        pending_logs.append(
                            f"'{name}': không tách được ID từ URL vừa dán — kiểm tra lại link."
                        )
                elif kind == "catalog_ready":
                    self._catalog_ready = True
                    self._stop_catalog_spinner()
                    for name, saved_items in self._catalog_items.items():
                        master_ids = {item.master_product_id for item in saved_items}
                        current_items = [
                            item
                            for item in self._engine.suggest_catalog(name, limit=200)
                            if item.master_product_id in master_ids
                        ]
                        if current_items:
                            self._catalog_items[name] = current_items
                            self._rebuild_tree_row(name)
                    if self._catalog_items:
                        self._persist_selected()
                    pending_logs.append(f"Catalog sẵn sàng: {msg[1]} sản phẩm.")
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
        win.geometry("560x560")
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
        body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
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

            ttk.Label(box, text="Username:").grid(row=0, column=0, sticky="w", padx=6, pady=3)
            u_entry = ttk.Entry(box, width=42)
            u_entry.insert(0, cfg.credentials.username)
            u_entry.grid(row=0, column=1, sticky="we", padx=6, pady=3)

            ttk.Label(box, text="Password:").grid(row=1, column=0, sticky="w", padx=6, pady=3)
            p_entry = ttk.Entry(box, width=42, show="•")
            p_entry.insert(0, cfg.credentials.password)
            p_entry.grid(row=1, column=1, sticky="we", padx=6, pady=3)

            show_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                box, text="Hiện", variable=show_var,
                command=lambda e=p_entry, v=show_var: e.config(show="" if v.get() else "•"),
            ).grid(row=1, column=2, padx=6)

            box.columnconfigure(1, weight=1)
            entries[site_id] = (u_entry, p_entry, cfg.credentials.username, cfg.credentials.password)

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(btn_row, text="💾 Lưu", command=lambda: self._save_credentials(entries, win)).pack(side="right")
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

"""MainWindow — GUI tkinter cho Drug Price Crawler.

Ví von: tkinter là "quầy lễ tân" chỉ nói được một luồng (UI thread), còn
crawl là "việc bếp núc" chạy asyncio. Không thể để lễ tân xuống bếp (block UI),
sẽ crawl chạy ở thread riêng và gửi tin nhắn về qua một khay (queue.Queue);
lễ tân định kỳ ra lấy khay (after 100ms) để cập nhật log/progress.

Luồng mới:
- Gõ tên thuốc → gợi ý từ CACHE (không crawl live khi đang gõ).
- Nút "Crawl mới" chạy thread fill cache; xong tự refresh gợi ý.
- Chọn thuốc → đưa vào danh sách đã chọn (mỗi thuốc 1 dòng, gộp giá nhiều nguồn).
- "Xuất CSV" ghi toàn bộ bản ghi đã chọn ra file.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

from crawlers.b2b import CRAWLER_REGISTRY
from crawlers.engine import CrawlerEngine
from gui import viewmodel as vm
from utils.config_loader import load_sites, load_watchlist_config, update_credentials
from utils.excel_writer import writer_for
from utils.models import CatalogItem

DEFAULT_CSV = "output/prices.csv"


class MainWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Drug Price Crawler")
        self.geometry("780x780")
        self.minsize(720, 720)

        self._msg_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._full_scan_worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._site_vars: dict[str, tk.BooleanVar] = {}

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
        self._build_ui()
        self.after(100, self._drain_queue)
        self.after(150, self._refresh_suggestions)
        self.after(200, self._maybe_refresh_catalog)

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
        ttk.Label(title_row, text="Drug Price Crawler", font=("", 13, "bold")).pack(side="left")
        ttk.Button(
            title_row, text="⚙ Sửa tài khoản", command=self._open_credentials_editor
        ).pack(side="right")

        # --- 2. Search row ---
        search_row = ttk.Frame(self)
        search_row.pack(fill="x", **pad)
        ttk.Label(search_row, text="Tìm thuốc:").pack(side="left")
        self._search = ttk.Entry(search_row)
        self._search.pack(side="left", fill="x", expand=True, padx=6)
        self._search.bind("<KeyRelease>", lambda _e: self._refresh_suggestions())

        sug_frame = ttk.Frame(self)
        sug_frame.pack(fill="x", padx=10)
        self._suggestions = tk.Listbox(sug_frame, height=8)
        sug_scroll = ttk.Scrollbar(sug_frame, command=self._suggestions.yview)
        self._suggestions.configure(yscrollcommand=sug_scroll.set)
        self._suggestions.pack(side="left", fill="both", expand=True)
        sug_scroll.pack(side="right", fill="y")
        self._suggestions.bind("<Double-Button-1>", lambda _e: self._add_selected())

        add_row = ttk.Frame(self)
        add_row.pack(fill="x", padx=10)
        ttk.Button(add_row, text="➕ Thêm", command=self._add_selected).pack(side="left")

        # --- 3b. Thêm bằng URL ---
        url_row = ttk.Frame(self)
        url_row.pack(fill="x", padx=10, pady=(4, 0))
        ttk.Label(url_row, text="Hoặc dán URL sản phẩm:").pack(side="left")
        self._url_entry = ttk.Entry(url_row)
        self._url_entry.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(url_row, text="🔗 Thêm từ URL", command=self._on_add_from_url).pack(side="left")

        # --- 4. Selected list ---
        ttk.Label(self, text="Đã chọn:", font=("", 10, "bold")).pack(anchor="w", padx=10, pady=(6, 0))
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=2)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("Tên thuốc", "Rẻ nhất", "Giá theo nguồn"),
            show="headings",
            height=10,
        )
        self._tree.heading("Tên thuốc", text="Tên thuốc")
        self._tree.heading("Rẻ nhất", text="Rẻ nhất ★")
        self._tree.heading("Giá theo nguồn", text="Giá theo nguồn")
        self._tree.column("Tên thuốc", width=220, anchor="w")
        self._tree.column("Rẻ nhất", width=170, anchor="w")
        self._tree.column("Giá theo nguồn", width=350, anchor="w")
        tree_scroll = ttk.Scrollbar(tree_frame, command=self._tree.yview)
        self._tree.configure(yscrollcommand=tree_scroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")

        sel_row = ttk.Frame(self)
        sel_row.pack(fill="x", padx=10)
        ttk.Button(sel_row, text="🗑 Xóa dòng", command=self._remove_selected).pack(side="left")
        ttk.Button(sel_row, text="Xóa hết", command=self._clear_all).pack(side="left", padx=6)
        ttk.Button(sel_row, text="💾 Xuất CSV", command=self._export_csv).pack(side="right")
        ttk.Button(sel_row, text="📊 Xuất Excel", command=self._export_excel).pack(side="right", padx=6)

        # --- 5. Crawl mới ---
        ttk.Separator(self).pack(fill="x", padx=10, pady=6)
        ttk.Label(self, text="Crawl mới (làm đầy cache):", font=("", 10, "bold")).pack(anchor="w", **pad)

        kw_row = ttk.Frame(self)
        kw_row.pack(fill="x", **pad)
        ttk.Label(kw_row, text="Từ khóa:").pack(side="left")
        self._keyword = ttk.Entry(kw_row)
        self._keyword.pack(side="left", fill="x", expand=True, padx=6)
        self._keyword.insert(0, "boganic")

        sites_frame = ttk.Frame(self)
        sites_frame.pack(fill="x", padx=10)
        for i, (site_id, cfg) in enumerate(self._sites.items()):
            var = tk.BooleanVar(value=cfg.enabled)
            self._site_vars[site_id] = var
            ttk.Checkbutton(
                sites_frame, text=f"{cfg.name} ({site_id})", variable=var
            ).grid(row=i // 2, column=i % 2, sticky="w", padx=4, pady=2)

        btns = ttk.Frame(self)
        btns.pack(fill="x", **pad)
        self._start_btn = ttk.Button(btns, text="▶ Crawl", command=self._on_start)
        self._start_btn.pack(side="left")
        self._stop_btn = ttk.Button(btns, text="■ Dừng", command=self._on_stop, state="disabled")
        self._stop_btn.pack(side="left", padx=6)

        self._progress = ttk.Progressbar(self, mode="determinate")
        self._progress.pack(fill="x", **pad)

        # --- 6. Full scan toàn bộ ---
        ttk.Separator(self).pack(fill="x", padx=10, pady=6)
        scan_row = ttk.Frame(self)
        scan_row.pack(fill="x", **pad)
        self._full_scan_btn = ttk.Button(
            scan_row, text="🔄 Scan lại toàn bộ (9 site)", command=self._on_full_scan
        )
        self._full_scan_btn.pack(side="left")
        ttk.Label(
            scan_row,
            text="Quét lại catalog cả 9 site (bỏ qua enabled + cache), báo xong bằng popup.",
        ).pack(side="left", padx=6)

        # --- 7. Log ---
        ttk.Label(self, text="Log:", font=("", 10, "bold")).pack(anchor="w", padx=10)
        log_frame = ttk.Frame(self)
        log_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._log = tk.Text(log_frame, height=8, wrap="word", state="disabled")
        log_scroll = ttk.Scrollbar(log_frame, command=self._log.yview)
        self._log.configure(yscrollcommand=log_scroll.set)
        self._log.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

    # ------------------------------------------------------- suggestions catalog
    def _refresh_suggestions(self) -> None:
        """Gợi ý theo tên canonical, dựa trên bảng catalog (tên+SKU thật của từng
        site, không phải cache giá cũ từ lịch sử search) — xem `_maybe_refresh_catalog`
        cho việc catalog được refresh định kỳ ra sao."""
        if self._engine is None:
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

    def _on_add_from_url(self) -> None:
        """Dán URL sản phẩm (copy từ 1 trong 9 site) → tra catalog cache theo
        source_url → tự suy nhóm canonical → lấy giá live cả nhóm (sync qua các site
        khác cùng tên), y hệt luồng chọn từ gợi ý."""
        if self._engine is None:
            messagebox.showinfo("Không có catalog", "Engine chưa sẵn sàng.")
            return
        url = self._url_entry.get().strip()
        if not url:
            messagebox.showinfo("Thiếu URL", "Dán URL sản phẩm cần thêm.")
            return
        item = self._engine.find_catalog_item_by_url(url)
        if item is None:
            messagebox.showinfo(
                "Không tìm thấy",
                "URL này chưa có trong catalog cache.\n"
                "Hãy bấm '🔄 Scan lại toàn bộ (9 site)' rồi thử lại.",
            )
            return
        seed = vm.search_seed_for(item.drug_name)
        candidates = self._engine.suggest_catalog(seed, limit=500)
        name, items = vm.resolve_group_for_item(item, candidates)
        self._start_add(name, items)
        self._url_entry.delete(0, "end")

    def _start_add(self, name: str, items: list[CatalogItem]) -> None:
        """Đuôi dùng chung cho mọi cách thêm sản phẩm (chọn từ gợi ý hoặc từ URL):
        check trùng → log → lấy giá live nền qua `_fetch_price_worker`."""
        if name in self._selected:
            messagebox.showinfo("Trùng", f"'{name}' đã có trong danh sách.")
            return
        self._append_log(f"Đang lấy giá live cho '{name}' ({len(items)} nguồn)...")
        threading.Thread(
            target=self._fetch_price_worker,
            args=(name, items),
            daemon=True,
        ).start()

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
            return
        self._msg_queue.put(("priced", name, records))

    def _on_priced(self, name: str, records: list) -> None:
        if not records:
            self._append_log(f"Không lấy được giá live nào cho '{name}'.")
            return
        self._selected[name] = records
        self._tree.insert(
            "", "end", iid=name,
            values=(name, vm.cheapest_label(records), vm.format_prices(records)),
        )
        self._append_log(f"Đã thêm '{name}' ({len(records)} bản ghi giá live).")

    def _on_scan_done(self, count: int, site_count: int, elapsed: float) -> None:
        messagebox.showinfo("Scan xong", vm.format_scan_summary(count, site_count, elapsed))

    def _remove_selected(self) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        for iid in sel:
            self._tree.delete(iid)
            self._selected.pop(iid, None)

    def _clear_all(self) -> None:
        for iid in list(self._selected.keys()):
            self._tree.delete(iid)
        self._selected.clear()

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

    # ---------------------------------------------------------------- actions
    def _on_start(self) -> None:
        if self._engine is None:
            messagebox.showwarning("Thiếu cấu hình", "Engine chưa sẵn sàng.")
            return
        keyword = self._keyword.get().strip()
        if not keyword:
            messagebox.showinfo("Thiếu từ khóa", "Nhập từ khóa cần crawl.")
            return
        selected = [sid for sid, var in self._site_vars.items() if var.get()]
        if not selected:
            messagebox.showinfo("Chưa chọn nguồn", "Chọn ít nhất một nguồn.")
            return

        self._cancel.clear()
        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._full_scan_btn.config(state="disabled")
        self._progress.config(value=0, maximum=len(selected))
        self._append_log(f"Bắt đầu crawl '{keyword}' trên {len(selected)} nguồn...")

        self._worker = threading.Thread(
            target=self._run_worker,
            args=(keyword, selected),
            daemon=True,
        )
        self._worker.start()

    def _on_stop(self) -> None:
        self._cancel.set()
        self._append_log("Đã yêu cầu dừng — sẽ dừng sau nguồn đang chạy.")
        self._stop_btn.config(state="disabled")

    def _is_busy(self) -> bool:
        return bool(
            (self._worker and self._worker.is_alive())
            or (self._full_scan_worker and self._full_scan_worker.is_alive())
        )

    def _on_full_scan(self) -> None:
        if self._engine is None:
            messagebox.showwarning("Thiếu cấu hình", "Engine chưa sẵn sàng.")
            return
        if self._is_busy():
            messagebox.showinfo("Đang chạy", "Đã có crawl/scan đang chạy — chờ xong rồi thử lại.")
            return

        site_ids = list(CRAWLER_REGISTRY.keys())
        self._full_scan_btn.config(state="disabled")
        self._start_btn.config(state="disabled")
        self._progress.config(value=0, maximum=len(site_ids))
        self._append_log(
            f"Bắt đầu scan lại toàn bộ catalog trên {len(site_ids)} site (bỏ qua enabled/tick)..."
        )

        self._full_scan_worker = threading.Thread(
            target=self._run_full_scan_worker,
            args=(site_ids,),
            daemon=True,
        )
        self._full_scan_worker.start()

    # ----------------------------------------------------------------- worker
    def _run_worker(self, keyword: str, site_ids: list[str]) -> None:
        """Chạy trong thread riêng. Mở engine riêng, crawl, đóng. UI đi qua queue."""
        def log(msg: str) -> None:
            self._msg_queue.put(("log", msg))

        def progress(done: int, total: int) -> None:
            self._msg_queue.put(("progress", done, total))

        try:
            engine = CrawlerEngine(log=log, use_cache=True)
            try:
                asyncio.run(engine.crawl(keyword, site_ids=site_ids, progress=progress))
            finally:
                engine.close()

            if self._cancel.is_set():
                self._msg_queue.put(("log", "Đã dừng."))
            else:
                self._msg_queue.put(("log", "Crawl xong — đã làm đầy cache."))
            self._msg_queue.put(("refresh", None))
            self._msg_queue.put(("done", None))
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI: {exc}"))
            self._msg_queue.put(("done", None))

    def _run_full_scan_worker(self, site_ids: list[str]) -> None:
        """Chạy trong thread riêng: ép crawl_catalog toàn bộ 9 site (bất kể enabled),
        báo tiến độ + log qua queue, và báo hoàn tất qua kind 'scan_done' (popup)."""
        def log(msg: str) -> None:
            self._msg_queue.put(("log", msg))

        def progress(done: int, total: int) -> None:
            self._msg_queue.put(("progress", done, total))

        started = time.time()
        try:
            engine = CrawlerEngine(log=log, use_cache=True)
            try:
                count = asyncio.run(
                    engine.crawl_catalog(site_ids=site_ids, force_refresh=True, progress=progress)
                )
            finally:
                engine.close()
            elapsed = time.time() - started
            self._msg_queue.put(("log", f"Scan toàn bộ xong: {count} mục catalog."))
            self._msg_queue.put(("refresh", None))
            self._msg_queue.put(("scan_done", count, len(site_ids), elapsed))
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI scan toàn bộ: {exc}"))
        finally:
            self._msg_queue.put(("full_scan_done", None))

    # ------------------------------------------------------------- queue pump
    def _drain_queue(self) -> None:
        try:
            while True:
                msg = self._msg_queue.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1])
                elif kind == "progress":
                    self._progress.config(value=msg[1], maximum=msg[2])
                elif kind == "refresh":
                    self._refresh_suggestions()
                elif kind == "priced":
                    self._on_priced(msg[1], msg[2])
                elif kind == "scan_done":
                    self._on_scan_done(msg[1], msg[2], msg[3])
                elif kind == "full_scan_done":
                    self._start_btn.config(state="normal")
                    self._full_scan_btn.config(state="normal")
                elif kind == "done":
                    self._start_btn.config(state="normal")
                    self._stop_btn.config(state="disabled")
                    self._full_scan_btn.config(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _append_log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.config(state="normal")
        self._log.insert("end", f"[{ts}] {message}\n")
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

    # ------------------------------------------------------- catalog refresh
    def _maybe_refresh_catalog(self) -> None:
        """1 user duy nhất, không cần real-time → catalog (tên+SKU, KHÔNG phải giá)
        chỉ cần tự refresh khi quá hạn (`watchlist.catalog_ttl_hours`, mặc định 30
        ngày), chạy nền lúc khởi động, không chặn UI. Giá luôn fetch live riêng lúc
        user chọn sản phẩm (`_fetch_price_worker`), không phụ thuộc catalog này."""
        if self._engine is None:
            return
        ttl_hours = load_watchlist_config().catalog_ttl_hours
        stale_sites: list[str] = []
        for site_id, cfg in self._sites.items():
            if not cfg.enabled or site_id not in CRAWLER_REGISTRY:
                continue
            source_name = getattr(CRAWLER_REGISTRY[site_id], "source_name", None)
            source_val = source_name.value if source_name else (cfg.name or site_id)
            age = self._engine.cache.catalog_age_hours(source_val)
            if age is None or age >= ttl_hours:
                stale_sites.append(site_id)
        if not stale_sites:
            return
        self._append_log(
            f"Catalog {len(stale_sites)} nguồn đã quá {ttl_hours:.0f}h — tự refresh nền..."
        )
        threading.Thread(
            target=self._refresh_catalog_worker,
            args=(stale_sites,),
            daemon=True,
        ).start()

    def _refresh_catalog_worker(self, site_ids: list[str]) -> None:
        def log(msg: str) -> None:
            self._msg_queue.put(("log", msg))

        try:
            engine = CrawlerEngine(log=log, use_cache=True)
            try:
                count = asyncio.run(engine.crawl_catalog(site_ids=site_ids))
            finally:
                engine.close()
        except Exception as exc:
            self._msg_queue.put(("log", f"LỖI refresh catalog: {exc}"))
            return
        self._msg_queue.put(("log", f"Catalog refresh xong: {count} mục."))
        self._msg_queue.put(("refresh", None))

    def destroy(self) -> None:
        if self._engine is not None:
            try:
                self._engine.close()
            except Exception:
                pass
        super().destroy()

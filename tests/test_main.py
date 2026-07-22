"""Tests cho main.main() — entry point chọn CLI/GUI theo sys.argv, chặn khi
trial hết hạn TRƯỚC khi mở GUI/chạy CLI. `TrialManager`/`MainWindow` được
import cục bộ bên trong `main()` (không phải module-level) nên monkeypatch
thẳng vào lớp/module gốc (`utils.trial_manager.TrialManager.check`,
`gui.main_window.MainWindow`) — import cục bộ vẫn trỏ tới cùng object đã vá.
"""

from __future__ import annotations

import os
import queue
import subprocess

import pytest

import main
from gui.main_window import MainWindow
from utils.models import SourceName
from utils.trial_manager import TrialStatus


class TestTkInputMethodEnvironment:
    """Verify that Tk selects the live Linux input-method bridge."""

    def test_running_fcitx_replaces_stale_ibus_xmodifier(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Replace a stale IBus selector when Fcitx5 is reachable."""
        monkeypatch.setattr(main.sys, "platform", "linux")
        monkeypatch.setenv("XMODIFIERS", "@im=ibus")
        monkeypatch.setattr(main.shutil, "which", lambda _name: "/usr/bin/fcitx5-remote")
        monkeypatch.setattr(
            main.subprocess,
            "run",
            lambda *_args, **_kwargs: subprocess.CompletedProcess([], returncode=0),
        )

        main._configure_tk_input_method()

        assert os.environ["XMODIFIERS"] == "@im=fcitx"

    def test_unavailable_fcitx_keeps_existing_input_method(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Preserve the desktop selector when Fcitx5 is unavailable."""
        monkeypatch.setattr(main.sys, "platform", "linux")
        monkeypatch.setenv("XMODIFIERS", "@im=ibus")
        monkeypatch.setattr(main.shutil, "which", lambda _name: None)

        main._configure_tk_input_method()

        assert os.environ["XMODIFIERS"] == "@im=ibus"


def _expired() -> TrialStatus:
    return TrialStatus(
        is_valid=False,
        days_remaining=0,
        is_first_run=False,
        message="Hết hạn dùng thử. Liên hệ Zalo: 0388279175 để mua license.",
    )


def _valid(is_first_run: bool = False) -> TrialStatus:
    return TrialStatus(
        is_valid=True,
        days_remaining=5,
        is_first_run=is_first_run,
        message="Bản dùng thử — còn 5 ngày.",
    )


class _FakeMainWindow:
    """Đếm số lần khởi tạo/mainloop — GUI thật KHÔNG được mở khi trial hết hạn."""

    instances = 0
    mainloop_calls = 0

    def __init__(self, *a, **kw) -> None:
        _FakeMainWindow.instances += 1

    def mainloop(self) -> None:
        _FakeMainWindow.mainloop_calls += 1


class _FakeTkRoot:
    """Stand in for hidden trial-dialog roots on headless test runners."""

    def withdraw(self) -> None:
        return None

    def destroy(self) -> None:
        return None


class TestMainBlocksOnExpiredTrial:
    def test_gui_path_returns_2_and_never_opens_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _FakeMainWindow.instances = 0
        _FakeMainWindow.mainloop_calls = 0
        shown: list[tuple[str, str]] = []

        monkeypatch.setattr("utils.trial_manager.TrialManager.check", lambda self: _expired())
        monkeypatch.setattr("gui.main_window.MainWindow", _FakeMainWindow)
        monkeypatch.setattr("tkinter.Tk", _FakeTkRoot)
        monkeypatch.setattr(
            "tkinter.messagebox.showerror",
            lambda title, msg: shown.append((title, msg)),
        )
        monkeypatch.setattr("sys.argv", ["main.py"])

        rc = main.main()

        assert rc == 2
        assert _FakeMainWindow.instances == 0, "GUI không được khởi tạo khi trial hết hạn"
        assert _FakeMainWindow.mainloop_calls == 0
        assert shown, "Phải hiện messagebox báo hết hạn"
        assert "Hết hạn" in shown[0][0] or "Hết hạn" in shown[0][1]

    def test_cli_path_prints_message_and_skips_cli_main(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli_called = False

        def fake_cli_main() -> int:
            nonlocal cli_called
            cli_called = True
            return 0

        monkeypatch.setattr("utils.trial_manager.TrialManager.check", lambda self: _expired())
        monkeypatch.setattr("cli.main", fake_cli_main)
        monkeypatch.setattr("sys.argv", ["main.py", "-k", "boganic"])

        rc = main.main()

        assert rc == 2
        assert not cli_called, "cli.main() không được chạy khi trial hết hạn"
        out = capsys.readouterr().out
        assert "Hết hạn" in out


class TestMainAllowsValidTrial:
    def test_gui_opens_when_trial_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # is_first_run=False -> main.py hiện messagebox.showinfo nhắc số ngày
        # còn lại (nhánh `if not trial.is_first_run`) — PHẢI mock, không thì
        # nó mở dialog thật, treo test vô thời hạn chờ click OK.
        _FakeMainWindow.instances = 0
        _FakeMainWindow.mainloop_calls = 0

        monkeypatch.setattr(
            "utils.trial_manager.TrialManager.check", lambda self: _valid(is_first_run=False)
        )
        monkeypatch.setattr("gui.main_window.MainWindow", _FakeMainWindow)
        monkeypatch.setattr("tkinter.Tk", _FakeTkRoot)
        monkeypatch.setattr("tkinter.messagebox.showinfo", lambda *a, **kw: None)
        monkeypatch.setattr("sys.argv", ["main.py"])

        rc = main.main()

        assert rc == 0
        assert _FakeMainWindow.instances == 1
        assert _FakeMainWindow.mainloop_calls == 1

    def test_first_run_skips_reminder_popup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lần chạy ĐẦU TIÊN (is_first_run=True) KHÔNG hiện popup nhắc số ngày
        còn lại (`if not trial.is_first_run` trong main.py) — popup đó chỉ
        hiện từ lần chạy thứ 2 trở đi. Vẫn phải mở GUI bình thường."""
        _FakeMainWindow.instances = 0
        shown: list[tuple[str, str]] = []

        monkeypatch.setattr(
            "utils.trial_manager.TrialManager.check", lambda self: _valid(is_first_run=True)
        )
        monkeypatch.setattr("gui.main_window.MainWindow", _FakeMainWindow)
        monkeypatch.setattr(
            "tkinter.messagebox.showinfo",
            lambda title, msg: shown.append((title, msg)),
        )
        monkeypatch.setattr("sys.argv", ["main.py"])

        rc = main.main()

        assert rc == 0
        assert _FakeMainWindow.instances == 1
        assert not shown, "Lần chạy đầu tiên không được hiện popup nhắc ngày"

    def test_second_run_shows_reminder_popup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Lần chạy thứ 2 trở đi (is_first_run=False) hiện popup nhắc số ngày
        dùng thử còn lại."""
        _FakeMainWindow.instances = 0
        shown: list[tuple[str, str]] = []

        monkeypatch.setattr(
            "utils.trial_manager.TrialManager.check", lambda self: _valid(is_first_run=False)
        )
        monkeypatch.setattr("gui.main_window.MainWindow", _FakeMainWindow)
        monkeypatch.setattr("tkinter.Tk", _FakeTkRoot)
        monkeypatch.setattr(
            "tkinter.messagebox.showinfo",
            lambda title, msg: shown.append((title, msg)),
        )
        monkeypatch.setattr("sys.argv", ["main.py"])

        rc = main.main()

        assert rc == 0
        assert _FakeMainWindow.instances == 1
        assert shown, "Từ lần chạy thứ 2 phải hiện popup nhắc số ngày còn lại"

    def test_cli_path_runs_when_trial_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cli_called = False

        def fake_cli_main() -> int:
            nonlocal cli_called
            cli_called = True
            return 0

        monkeypatch.setattr("utils.trial_manager.TrialManager.check", lambda self: _valid())
        monkeypatch.setattr("cli.main", fake_cli_main)
        monkeypatch.setattr("sys.argv", ["main.py", "-k", "boganic"])

        rc = main.main()

        assert rc == 0
        assert cli_called


class TestListingEditValidation:
    def test_opening_product_detail_twice_keeps_only_one_edit_screen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nút Sửa mở bảng chi tiết; bấm nhiều lần không được chồng nhiều bảng."""

        class FakeWidget:
            created_toplevels: list["FakeWidget"] = []

            def __init__(self, *_args, **_kwargs) -> None:
                self.exists = True
                self.lift_calls = 0

            def title(self, *_args) -> None:
                return None

            def transient(self, *_args) -> None:
                return None

            def pack(self, *_args, **_kwargs) -> "FakeWidget":
                return self

            def insert(self, *_args, **_kwargs) -> None:
                return None

            def configure(self, *_args, **_kwargs) -> None:
                return None

            def heading(self, *_args, **_kwargs) -> None:
                return None

            def column(self, *_args, **_kwargs) -> None:
                return None

            def yview(self, *_args, **_kwargs) -> None:
                return None

            def set(self, *_args, **_kwargs) -> None:
                return None

            def bind(self, *_args, **_kwargs) -> None:
                return None

            def destroy(self) -> None:
                self.exists = False

            def winfo_exists(self) -> bool:
                return self.exists

            def lift(self) -> None:
                self.lift_calls += 1

            def focus_force(self) -> None:
                return None

        class FakeToplevel(FakeWidget):
            def __init__(self, *_args, **_kwargs) -> None:
                super().__init__()
                self.created_toplevels.append(self)

        for path in (
            "gui.main_window.ttk.Frame",
            "gui.main_window.ttk.Entry",
            "gui.main_window.ttk.Treeview",
            "gui.main_window.ttk.Scrollbar",
            "gui.main_window.ttk.Label",
            "gui.main_window.ttk.Button",
        ):
            monkeypatch.setattr(path, FakeWidget)
        monkeypatch.setattr("gui.main_window.tk.Toplevel", FakeToplevel)

        window = type("WindowStub", (), {})()
        window._catalog_items = {}
        window._selected = {}
        window._site_descriptors = lambda: []
        window._site_order = lambda: []
        window._center_toplevel = lambda *_args: None
        window._colored_button = lambda *_args, **_kwargs: FakeWidget()
        window._copyable_text = lambda *_args, **_kwargs: FakeWidget()
        window._bind_tree_copy = lambda *_args, **_kwargs: None
        window._configure_status_tags = lambda *_args, **_kwargs: None

        MainWindow._show_product_detail(window, "Alaxan", items=[], records=[])
        MainWindow._show_product_detail(window, "Alaxan", items=[], records=[])

        assert len(FakeToplevel.created_toplevels) == 1
        assert FakeToplevel.created_toplevels[0].lift_calls == 1

    def test_price_text_is_stronger_than_missing_product_text(self) -> None:
        configured: dict[str, dict] = {}

        class FakeTree:
            def tag_configure(self, kind: str, **options) -> None:
                configured[kind] = options

        window = type("WindowStub", (), {"_ui_font": ("Noto Sans", 10)})()

        MainWindow._configure_status_tags(window, FakeTree())

        assert configured["price"]["font"] == ("Noto Sans", 10, "bold")
        assert configured["missing"]["font"] == ("Noto Sans", 9, "normal")

    def test_verify_step_has_back_button_and_restores_entered_urls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bước kiểm tra phải quay lại form URL mà không làm mất dữ liệu đã dán."""

        class FakeWidget:
            entries: list["FakeWidget"] = []

            def __init__(self, *_args, **kwargs) -> None:
                self.value = ""
                self.command = kwargs.get("command")

            def pack(self, *_args, **_kwargs) -> "FakeWidget":
                return self

            def grid(self, *_args, **_kwargs) -> "FakeWidget":
                return self

            def columnconfigure(self, *_args, **_kwargs) -> None:
                return None

            def insert(self, *args, **_kwargs) -> None:
                if len(args) >= 2:
                    self.value = args[1]

            def get(self) -> str:
                return self.value

            def configure(self, *_args, **_kwargs) -> None:
                return None

            def heading(self, *_args, **_kwargs) -> None:
                return None

            def column(self, *_args, **_kwargs) -> None:
                return None

            def yview(self, *_args, **_kwargs) -> None:
                return None

            def bind(self, *_args, **_kwargs) -> None:
                return None

            def set(self, *_args, **_kwargs) -> None:
                return None

            def destroy(self) -> None:
                return None

        class FakeEntry(FakeWidget):
            def __init__(self, *_args, **kwargs) -> None:
                super().__init__(*_args, **kwargs)
                self.entries.append(self)

        class FakeButton(FakeWidget):
            buttons: list["FakeButton"] = []

            def __init__(self, _parent, text="", command=None, **kwargs) -> None:
                super().__init__(command=command, **kwargs)
                self.text = text
                self.buttons.append(self)

        class FakeWindow:
            def winfo_children(self) -> list:
                return []

            def title(self, *_args) -> None:
                return None

            def destroy(self) -> None:
                return None

        class FakeThread:
            def __init__(self, *_args, **_kwargs) -> None:
                return None

            def start(self) -> None:
                return None

        for path in (
            "gui.main_window.ttk.Frame",
            "gui.main_window.ttk.Label",
            "gui.main_window.ttk.Treeview",
            "gui.main_window.ttk.Scrollbar",
        ):
            monkeypatch.setattr(path, FakeWidget)
        monkeypatch.setattr("gui.main_window.ttk.Entry", FakeEntry)
        monkeypatch.setattr("gui.main_window.ttk.Button", FakeButton)
        monkeypatch.setattr("gui.main_window.threading.Thread", FakeThread)

        window = type("WindowStub", (), {})()
        window._site_order = lambda: [
            ("thuocsi", SourceName.THUOCSI, "Thuốc Sỉ")
        ]
        window._center_toplevel = lambda *_args: None
        window._bind_tree_copy = lambda *_args, **_kwargs: None
        window._colored_button = lambda *_args, **_kwargs: FakeWidget()
        window._verify_new_product_worker = lambda *_args: None
        window._configure_status_tags = lambda *_args, **_kwargs: None
        window._render_product_url_form = lambda form_win, initial_urls=None: (
            MainWindow._render_product_url_form(window, form_win, initial_urls)
        )

        url = (
            "https://thuocsi.vn/product/"
            "medx-alaxan-united-h10v10v-bam?isAvailable=false"
        )
        MainWindow._open_verify_new_product(
            window, {"thuocsi": url}, "Alaxan", FakeWindow()
        )

        back = next(button for button in FakeButton.buttons if button.text == "Quay lại")
        back.command()

        assert any(entry.value == url for entry in FakeEntry.entries)

    def test_opening_edit_twice_keeps_only_one_dialog(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nhấp đúp/click Sửa nhiều lần không được chồng nhiều dialog edit."""

        class FakeWidget:
            created_toplevels: list["FakeWidget"] = []

            def __init__(self, *_args, **_kwargs) -> None:
                self.exists = True
                self.lift_calls = 0

            def title(self, *_args) -> None:
                return None

            def resizable(self, *_args) -> None:
                return None

            def transient(self, *_args) -> None:
                return None

            def grab_set(self) -> None:
                return None

            def pack(self, *_args, **_kwargs) -> "FakeWidget":
                return self

            def grid(self, *_args, **_kwargs) -> "FakeWidget":
                return self

            def columnconfigure(self, *_args, **_kwargs) -> None:
                return None

            def insert(self, *_args, **_kwargs) -> None:
                return None

            def get(self) -> str:
                return ""

            def destroy(self) -> None:
                self.exists = False

            def winfo_exists(self) -> bool:
                return self.exists

            def lift(self) -> None:
                self.lift_calls += 1

            def focus_force(self) -> None:
                return None

        class FakeToplevel(FakeWidget):
            def __init__(self, *_args, **_kwargs) -> None:
                super().__init__()
                self.created_toplevels.append(self)

        monkeypatch.setattr("gui.main_window.tk.Toplevel", FakeToplevel)
        monkeypatch.setattr("gui.main_window.ttk.Frame", FakeWidget)
        monkeypatch.setattr("gui.main_window.ttk.Label", FakeWidget)
        monkeypatch.setattr("gui.main_window.ttk.Entry", FakeWidget)
        monkeypatch.setattr("gui.main_window.ttk.Button", FakeWidget)

        window = type("WindowStub", (), {})()
        window._engine = object()
        window._saving_manual_product = False
        window._center_toplevel = lambda *_args: None
        window._colored_button = lambda *_args, **_kwargs: FakeWidget()

        args = {
            "name": "Alaxan",
            "master_id": "MP1",
            "site_id": "thuocsi",
            "source": SourceName.THUOCSI,
            "site_display": "Thuốc Sỉ",
            "current_url": (
                "https://thuocsi.vn/product/"
                "medx-alaxan-united-h10v10v-bam?isAvailable=false"
            ),
        }
        MainWindow._open_edit_listing_dialog(window, **args)
        MainWindow._open_edit_listing_dialog(window, **args)

        assert len(FakeToplevel.created_toplevels) == 1
        assert FakeToplevel.created_toplevels[0].lift_calls == 1

    def test_invalid_product_url_does_not_save_name_link_or_id(self) -> None:
        class FakeEngine:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def rename_product(self, *_args) -> None:
                self.calls.append("rename")

            def set_manual_listing(self, *_args):
                self.calls.append("listing")
                return None

        engine = FakeEngine()
        window = type("WindowStub", (), {})()
        window._engine = engine
        window._msg_queue = queue.Queue()

        MainWindow._save_listing_edit_worker(
            window,
            "Alaxan cũ",
            "MP1",
            "chothuoc247",
            SourceName.CHOTHUOC247,
            "Alaxan mới",
            "https://example.com/san-pham/5026",
            "https://chothuoc247.vn/san-pham/5027",
        )

        assert engine.calls == []
        messages = [window._msg_queue.get_nowait(), window._msg_queue.get_nowait()]
        assert messages[0][0] == "log"
        assert messages[1][0] == "listing_edit_done"
        assert messages[1][-2:] == (False, False)

"""Tests cho main.main() — entry point chọn CLI/GUI theo sys.argv, chặn khi
trial hết hạn TRƯỚC khi mở GUI/chạy CLI. `TrialManager`/`MainWindow` được
import cục bộ bên trong `main()` (không phải module-level) nên monkeypatch
thẳng vào lớp/module gốc (`utils.trial_manager.TrialManager.check`,
`gui.main_window.MainWindow`) — import cục bộ vẫn trỏ tới cùng object đã vá.
"""

from __future__ import annotations

import os
import subprocess

import pytest

import main
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


class TestMainBlocksOnExpiredTrial:
    def test_gui_path_returns_2_and_never_opens_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _FakeMainWindow.instances = 0
        _FakeMainWindow.mainloop_calls = 0
        shown: list[tuple[str, str]] = []

        monkeypatch.setattr("utils.trial_manager.TrialManager.check", lambda self: _expired())
        monkeypatch.setattr("gui.main_window.MainWindow", _FakeMainWindow)
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

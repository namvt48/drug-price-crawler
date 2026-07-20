"""Entry point.

- Không tham số  → mở GUI (dùng khi double-click .exe).
- Có tham số     → chạy CLI (vd: main.py -k boganic).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


def _configure_tk_input_method() -> None:
    """Point Tk at a running Fcitx5 XIM server before the first root opens.

    Desktop launchers can leave ``XMODIFIERS=@im=ibus`` behind after the user
    switches to Fcitx5. Tk uses XIM directly, so that stale selector prevents
    composed Vietnamese text even though GTK and Qt applications still work.
    """
    if not sys.platform.startswith("linux"):
        return
    if os.environ.get("XMODIFIERS") == "@im=fcitx":
        return

    remote = shutil.which("fcitx5-remote")
    if remote is None:
        return
    try:
        probe = subprocess.run(
            (remote,),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if probe.returncode == 0:
        os.environ["XMODIFIERS"] = "@im=fcitx"


def main() -> int:
    from utils.trial_manager import TrialManager

    trial = TrialManager().check()

    if len(sys.argv) > 1:
        print(trial.message, flush=True)
        if not trial.is_valid:
            return 2
        from cli import main as cli_main

        return cli_main()

    _configure_tk_input_method()

    if not trial.is_valid:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Hết hạn dùng thử", trial.message)
        root.destroy()
        return 2

    if not trial.is_first_run:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo("PharmaPrice", trial.message)
        root.destroy()

    from gui.main_window import MainWindow

    MainWindow().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Entry point.

- Không tham số  → mở GUI (dùng khi double-click .exe).
- Có tham số     → chạy CLI (vd: main.py -k boganic).
"""

from __future__ import annotations

import sys


def main() -> int:
    from utils.trial_manager import TrialManager

    trial = TrialManager().check()

    if len(sys.argv) > 1:
        print(trial.message, flush=True)
        if not trial.is_valid:
            return 2
        from cli import main as cli_main

        return cli_main()

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
        messagebox.showinfo("Drug Price Crawler", trial.message)
        root.destroy()

    from gui.main_window import MainWindow

    MainWindow().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

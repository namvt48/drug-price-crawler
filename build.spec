# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — build 1 file .exe portable cho Windows.
# Chạy: pyinstaller build.spec   (trên Windows)
#
# LƯU Ý: KHÔNG nhúng config/accounts.yaml (chứa mật khẩu) vào .exe.
# accounts.yaml phải đặt CẠNH file .exe: dist/config/accounts.yaml
# (config_loader.app_base_dir() đọc config cạnh .exe khi frozen).

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# sv_ttk: theme ttk kiểu Windows 11 (xem gui/main_window.py) — bundle kèm
# file .tcl/.png riêng (sv.tcl, theme/light.tcl, spritesheet_light.png...),
# không nằm trong .py nên PyInstaller không tự thấy, phải khai báo thủ công.
datas = collect_data_files('sv_ttk')

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'selectolax.parser',
        'selectolax.lexbor',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=['pandas', 'numpy', 'matplotlib', 'playwright'],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DrugPriceCrawler',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # --windowed: không hiện cửa sổ console đen
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

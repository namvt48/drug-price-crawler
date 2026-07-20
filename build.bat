@echo off
REM ===========================================================================
REM  Build Drug Price Crawler -> 1 file .exe portable (chay tren Windows).
REM ===========================================================================
echo [1/4] Cai dependencies...
pip install -r requirements.txt
pip install pyinstaller==6.10.0

echo [2/4] Build .exe bang PyInstaller...
pyinstaller build.spec --noconfirm

echo [3/4] Chuan bi config va catalog canh .exe...
if not exist dist\config mkdir dist\config
if not exist dist\output mkdir dist\output
if not exist dist\config\accounts.yaml (
    copy config\accounts.example.yaml dist\config\accounts.yaml
    echo    -^> Da tao dist\config\accounts.yaml tu example. NHO dien tai khoan that!
)
copy /Y config\name_aliases.yaml dist\config\name_aliases.yaml >nul
if not exist output\catalog_master_entity_resolved.xlsx (
    echo ERROR: Khong tim thay output\catalog_master_entity_resolved.xlsx
    exit /b 1
)
copy /Y output\catalog_master_entity_resolved.xlsx dist\output\catalog_master_entity_resolved.xlsx >nul

echo [4/4] Xong!
echo    Output: dist\DrugPriceCrawler.exe
echo    Nho dien tai khoan vao: dist\config\accounts.yaml
echo    Catalog san pham: dist\output\catalog_master_entity_resolved.xlsx
pause

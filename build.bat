@echo off
REM ===========================================================================
REM  Build Drug Price Crawler -> 1 file .exe portable (chay tren Windows).
REM ===========================================================================
echo [1/4] Cai dependencies...
pip install -r requirements.txt
pip install pyinstaller==6.10.0

echo [2/4] Build .exe bang PyInstaller...
pyinstaller build.spec --noconfirm

echo [3/4] Chuan bi config canh .exe...
if not exist dist\config mkdir dist\config
if not exist dist\config\accounts.yaml (
    copy config\accounts.example.yaml dist\config\accounts.yaml
    echo    -^> Da tao dist\config\accounts.yaml tu example. NHO dien tai khoan that!
)

echo [4/4] Xong!
echo    Output: dist\DrugPriceCrawler.exe
echo    Nho dien tai khoan vao: dist\config\accounts.yaml
pause

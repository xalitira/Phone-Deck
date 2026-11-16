@echo off
cd /d "%~dp0"
python -m pip install flask winsdk pyautogui pywebview pystray pillow pyinstaller
python -m PyInstaller --onefile --noconsole --collect-all webview --icon TelefonDeck.ico --add-data "TelefonDeck.ico;." --name PhoneDeck server.py
echo.
if exist "dist\PhoneDeck.exe" (
  echo HAZIR: %~dp0dist\PhoneDeck.exe
  explorer "%~dp0dist"
) else (
  echo HATA: exe olusmadi. Yukaridaki hata satirlarini kontrol et.
)
pause

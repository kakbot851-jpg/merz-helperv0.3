@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "Merz Helper" --collect-all pynput --collect-all pyperclip merz_helper.py
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo EXE created: dist\Merz Helper.exe
pause

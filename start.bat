@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found. Install Python 3.10+ and tick "Add to PATH".
  pause
  exit /b 1
)
python demo_seed.py
echo.
echo Starting workbench at http://127.0.0.1:8899  (Ctrl+C to stop)
python app.py
pause

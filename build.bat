@echo off
cd /d "%~dp0"
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name Sub2Sup --icon assets\app.ico --paths src ^
  --distpath dist --workpath build app_main.py
if errorlevel 1 (
  echo.
  echo Build failed.
  pause
  exit /b 1
)
copy /y "README.md" "dist\使用说明.txt" >nul
echo.
echo Build OK: %~dp0dist\Sub2Sup.exe
pause

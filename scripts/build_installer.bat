@echo off
setlocal
cd /d "%~dp0\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_installer.ps1"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Installer build failed with exit code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo [DONE] Installer build finished successfully.
pause

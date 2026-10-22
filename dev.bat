@echo off
setlocal
cd /d "%~dp0"
echo Launching Rainy Tools (Native Dev Mode)...
".venv\Scripts\python.exe" src\main.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo App exited with error code %ERRORLEVEL%.
    pause
)

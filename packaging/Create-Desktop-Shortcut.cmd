@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1"
if errorlevel 1 (
    echo Shortcut creation failed.
    pause
    exit /b 1
)
pause

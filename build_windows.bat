@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_windows.ps1"
if errorlevel 1 goto build_failed

echo.
echo Build completed successfully.
echo Open dist\EnglishWorkBench and run EnglishWorkBench.exe.
echo A distributable ZIP is also available at release\EnglishWorkBench_Windows_x64.zip.
goto finish

:build_failed
echo.
echo Build failed. Review the messages above.

:finish
echo.
pause

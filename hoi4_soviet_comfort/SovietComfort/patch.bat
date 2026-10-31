@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (py tools\patch_vanilla.py || python tools\patch_vanilla.py) else (py tools\patch_vanilla.py "%~1" || python tools\patch_vanilla.py "%~1")
pause

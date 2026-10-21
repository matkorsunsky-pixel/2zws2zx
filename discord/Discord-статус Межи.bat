@echo off
chcp 65001 >nul
title Межа - Discord
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0discord-status.ps1"
pause

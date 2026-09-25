@echo off
chcp 65001 >nul 2>&1
title Final Trade - TDX 数据同步
echo ============================================
echo  双击运行即可同步 TDX 数据到服务器
echo ============================================
echo.
powershell -ExecutionPolicy Bypass -File "%~dp0sync-tdx-data.ps1"
echo.
pause

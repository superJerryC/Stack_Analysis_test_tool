@echo off
cd /d "%~dp0"
title AI 股票分析工具
echo ================================
echo   AI 股票分析工具 啟動中...
echo   自動以 Chrome 開啟瀏覽器
echo   關閉此視窗即可停止伺服器
echo ================================
"C:\Users\user\AppData\Local\Programs\Python\Python314\python.exe" app.py
pause

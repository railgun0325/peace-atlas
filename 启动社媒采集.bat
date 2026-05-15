@echo off
cd /d "%~dp0\analysis-worker"
title Japan Anti-War Intel - Social Collector

:: 代理配置 (从 .env 读取或手动设置)
set PROXY_URL=http://127.0.0.1:7897

echo ============================================================
echo   日本反战情报 - 社媒采集器 (独立模式)
echo ============================================================
echo.
echo   Twitter/X: Nitter RSS (无需登录) - 9个监控账号
echo   Instagram: Instagrapi (需要登录) - 4个账号 + 8个标签
echo.
echo   每10分钟自动采集
echo   Ctrl+C 停止
echo.

:loop
echo [%date% %time%] >>> 采集开始...
echo.
python standalone_social_collector.py 2>&1
echo.
echo [%date% %time%] <<< 完成, 等待10分钟...
echo.
timeout /t 600 /nobreak >nul
goto loop

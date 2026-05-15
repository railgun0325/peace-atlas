@echo off
setlocal
cd /d "%~dp0"
title japan-antiwar-intel - social collector

echo ============================================================
echo  Social Collector (Docker)
echo ============================================================
echo.
echo This script runs the collector inside analysis-worker container.
echo Ensure docker compose services are up and .env is configured.
echo.

docker compose run --rm analysis-worker python standalone_social_collector.py --loop --interval 600

if errorlevel 1 (
  echo.
  echo Collector exited with error. Check docker logs and .env.
  pause
)

endlocal

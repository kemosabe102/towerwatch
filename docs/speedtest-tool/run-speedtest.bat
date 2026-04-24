@echo off
REM Towerwatch — Manual speedtest trigger (Windows)
REM Requires: Tailscale installed + OpenSSH (built into Windows 10/11).
REM Ask the operator for the Pi's Tailscale IP.

setlocal

set /p YOURNAME="Your name (recorded with the result): "
if "%YOURNAME%"=="" set YOURNAME=unknown

set /p PI_IP="Pi Tailscale IP (ask the operator): "
if "%PI_IP%"=="" (
    echo No IP entered. Exiting.
    pause
    exit /b 1
)

echo.
echo Connecting to %PI_IP% ...
ssh admin@%PI_IP% towerwatch-speedtest --triggered-by %YOURNAME%

echo.
pause

@echo off
REM Towerwatch — Manual speedtest trigger (Windows)
REM Requires: Tailscale installed + signed in + you're on the operator's
REM Tailscale ACL for this Pi. OpenSSH is built into Windows 10/11.
REM
REM Your Tailscale identity is recorded automatically — no name prompt.

setlocal

set /p PI_IP="Pi Tailscale IP (ask the operator): "
if "%PI_IP%"=="" (
    echo No IP entered. Exiting.
    pause
    exit /b 1
)

echo.
echo Connecting to %PI_IP% ...
ssh towerwatch-user@%PI_IP%

echo.
pause

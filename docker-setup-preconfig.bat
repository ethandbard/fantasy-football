@echo off
REM Fantasy Football Bot - Pre-configured Docker Setup for Windows

echo 🏈 Fantasy Football Discord Bot - Pre-configured Setup
echo =====================================================
echo.

REM Check if Docker is available
docker --version >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker is not installed or not in PATH
    echo    Please install Docker Desktop from: https://www.docker.com/products/docker-desktop
    pause
    exit /b 1
)

echo ✅ Docker is installed
echo 📋 Reading configuration from config.env
echo 🚀 Starting Fantasy Football Bot...
echo.

REM Compose ships as a docker plugin ("docker compose"). The standalone v1
REM binary is absent from current installs, so detect the plugin first.
set COMPOSE=docker compose
docker compose version >nul 2>&1
if errorlevel 1 set COMPOSE=docker-compose

REM Only fantasy-bot. A bare "up" also starts cloudflared, which claims the
REM same named tunnel the VPS runs.
%COMPOSE% up -d --build fantasy-bot

if errorlevel 1 (
    echo ❌ Failed to start the bot. Check the error messages above.
    pause
    exit /b 1
)

echo.
echo ✅ Bot started successfully!
echo.
echo 📊 Useful commands:
echo    View logs:     %COMPOSE% logs -f fantasy-bot
echo    Stop bot:      %COMPOSE% down
echo    Restart bot:   %COMPOSE% restart fantasy-bot
echo    Bot status:    %COMPOSE% ps
echo.
echo 🎯 The bot is now running and will send messages to Discord automatically!
echo    Settings come from config.env; restart after editing it.
echo.
pause

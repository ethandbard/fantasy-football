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
echo 📋 Configuration is pre-built into the container
echo 🚀 Starting Fantasy Football Bot...
echo.

REM Build and start the container
docker-compose up -d --build

if errorlevel 1 (
    echo ❌ Failed to start the bot. Check the error messages above.
    pause
    exit /b 1
)

echo.
echo ✅ Bot started successfully!
echo.
echo 📊 Useful commands:
echo    View logs:     docker-compose logs -f fantasy-bot
echo    Stop bot:      docker-compose down
echo    Restart bot:   docker-compose restart fantasy-bot
echo    Bot status:    docker-compose ps
echo.
echo 🎯 The bot is now running and will send messages to Discord automatically!
echo    No configuration needed - everything is pre-set!
echo.
pause

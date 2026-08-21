#!/bin/bash
# Fantasy Football Bot - Pre-configured Docker Setup for Linux/Mac

echo "🏈 Fantasy Football Discord Bot - Pre-configured Setup"
echo "====================================================="

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker is not installed. Please install Docker first:"
    echo "   - Windows/Mac: https://www.docker.com/products/docker-desktop"
    echo "   - Linux: https://docs.docker.com/engine/install/"
    exit 1
fi

# Check if Docker Compose is installed
# Compose ships as a docker plugin ("docker compose"). The standalone v1
# binary ("docker-compose") is absent from current installs, so prefer the
# plugin and fall back only if it is missing.
if docker compose version &> /dev/null; then
    COMPOSE="docker compose"
elif command -v docker-compose &> /dev/null; then
    COMPOSE="docker-compose"
else
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

echo "✅ Docker is installed"
echo "📋 Reading configuration from config.env"
echo "🚀 Starting Fantasy Football Bot..."

# Bare `up` starts fantasy-bot only. The cloudflared sidecar is behind the
# `tunnel` profile and stays down unless that profile is requested.
$COMPOSE up -d --build

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Bot started successfully!"
    echo ""
    echo "📊 Useful commands:"
    echo "   View logs:     $COMPOSE logs -f fantasy-bot"
    echo "   Stop bot:      $COMPOSE down"
    echo "   Restart bot:   $COMPOSE restart fantasy-bot"
    echo "   Bot status:    $COMPOSE ps"
    echo ""
    echo "🎯 The bot is now running and will send messages to Discord automatically!"
    echo "   Settings come from config.env; restart after editing it."
else
    echo "❌ Failed to start the bot. Check the error messages above."
    exit 1
fi

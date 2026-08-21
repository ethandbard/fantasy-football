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
if ! command -v docker-compose &> /dev/null; then
    echo "❌ Docker Compose is not installed. Please install Docker Compose first."
    exit 1
fi

echo "✅ Docker is installed"
echo "📋 Reading configuration from config.env"
echo "🚀 Starting Fantasy Football Bot..."

# Build and start the container
docker-compose up -d --build

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Bot started successfully!"
    echo ""
    echo "📊 Useful commands:"
    echo "   View logs:     docker-compose logs -f fantasy-bot"
    echo "   Stop bot:      docker-compose down"
    echo "   Restart bot:   docker-compose restart fantasy-bot"
    echo "   Bot status:    docker-compose ps"
    echo ""
    echo "🎯 The bot is now running and will send messages to Discord automatically!"
    echo "   Settings come from config.env; restart after editing it."
else
    echo "❌ Failed to start the bot. Check the error messages above."
    exit 1
fi

#!/bin/bash
set -e

echo "🚀 Starting UkrTab deployment..."

# 1. Pull latest code from git
git pull origin main

# 2. Build and restart containers in background
docker compose up -d --build --remove-orphans

echo "✅ UkrTab deployed and running successfully!"
docker compose ps

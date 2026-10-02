#!/bin/bash
# Keeps the Discord bot running 24/7 - restarts it if it crashes. Mac/Linux.
cd "$(dirname "$0")"
while true; do
  echo "[$(date)] Starting bot..."
  # caffeinate (Mac) stops the computer sleeping while the bot runs
  if command -v caffeinate >/dev/null; then caffeinate -i python3 bot.py; else python3 bot.py; fi
  echo "Bot stopped. Restarting in 15 seconds... (Ctrl+C to stop)"
  sleep 15
done

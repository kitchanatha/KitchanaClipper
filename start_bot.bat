@echo off
REM Keeps the Discord bot running 24/7 - restarts it if it crashes.
cd /d "%~dp0"
:loop
echo [%date% %time%] Starting bot...
python bot.py
echo Bot stopped. Restarting in 15 seconds... (close this window to stop)
timeout /t 15 /nobreak >nul
goto loop

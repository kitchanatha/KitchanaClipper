@echo off
REM Keeps the Discord bot running 24/7 - restarts it if it crashes.
cd /d "%~dp0"
REM Prefer the "py" launcher: plain "python" can hit the Microsoft Store placeholder.
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
:loop
echo [%date% %time%] Starting bot...
%PY% bot.py
echo Bot stopped. Restarting in 15 seconds... (close this window to stop)
timeout /t 15 /nobreak >nul
goto loop

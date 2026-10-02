@echo off
REM One-click setup for the Faceless Shorts Pipeline (Windows)
cd /d "%~dp0"
echo ==== Faceless Shorts Pipeline - setup ====
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] Python is not installed.
  echo     Install it from https://www.python.org/downloads/
  echo     IMPORTANT: tick "Add Python to PATH" in the installer, then run setup.bat again.
  pause
  exit /b 1
)
echo [OK] Python found

where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo [..] Installing FFmpeg with winget - answer Y if it asks...
  winget install --id Gyan.FFmpeg -e
  echo.
  echo [!] FFmpeg installed. CLOSE this window and run setup.bat again so Windows can find it.
  pause
  exit /b 0
)
echo [OK] FFmpeg found

echo [..] Installing Python packages...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [X] Package install failed - copy the error above and send it to Claude.
  pause
  exit /b 1
)
echo [OK] Packages installed

if not exist fonts\Anton-Regular.ttf (
  echo [..] Downloading Anton caption font from Google Fonts...
  curl -sL -o fonts\Anton-Regular.ttf https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf
)
if exist fonts\Anton-Regular.ttf (echo [OK] Caption font ready) else (echo [!] Font download failed - download Anton from fonts.google.com into the fonts folder)

if not exist .env (
  copy .env.example .env >nul
  echo [OK] Created .env - open it in Notepad and paste your API keys
) else (
  echo [OK] .env already exists
)

echo.
echo [..] Building a test video (no API keys needed)...
python run.py test-video
echo.
echo ==== Setup finished. If test_video.mp4 opened with captions, everything works. ====
pause

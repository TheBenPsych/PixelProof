@echo off
setlocal
cd /d "%~dp0"
title PixelProof
if not exist ".venv\Scripts\python.exe" (
  echo PixelProof is not set up yet. Double-click install.bat first.
  pause
  exit /b 1
)
set "VPY=.venv\Scripts\python.exe"

rem After an update, add any new parts PixelProof needs (only runs when something is missing).
"%VPY%" -c "import reportlab, cv2" >nul 2>nul
if errorlevel 1 (
  echo Adding a few new parts PixelProof needs. This happens once and takes a minute...
  "%VPY%" -m pip install -q -r requirements.txt
  "%VPY%" -m pip install -q -e . >nul
)

"%VPY%" -m pixelproof app
if errorlevel 1 (
  echo.
  echo Something went wrong. Take a screenshot of this window and send it to Claude.
  pause
)

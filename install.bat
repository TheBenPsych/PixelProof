@echo off
setlocal
cd /d "%~dp0"
title PixelProof setup
echo.
echo  Setting up PixelProof.
echo  The first time, this downloads about 3 GB and can take 10 to 20 minutes.
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY (
  echo  Python was not found.
  echo  Install it from https://www.python.org/downloads/
  echo  and tick "Add python.exe to PATH" on the first screen. Then run this again.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo  Making a private Python space for PixelProof in the .venv folder...
  %PY% -m venv .venv
  if errorlevel 1 goto fail
)
set "VPY=.venv\Scripts\python.exe"

"%VPY%" -m pip install --upgrade pip
if errorlevel 1 goto fail

echo.
echo  Installing PyTorch with NVIDIA graphics card support (the big download)...
"%VPY%" -m pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 goto fail

echo.
echo  Installing the rest...
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto fail
"%VPY%" -m pip install -e .
if errorlevel 1 goto fail

echo.
echo  Checking everything (this also downloads the AI model once, about 350 MB)...
"%VPY%" -m pixelproof doctor
echo.
echo  Setup finished. Double-click PixelProof.bat to start.
pause
exit /b 0

:fail
echo.
echo  Something went wrong during setup.
echo  Take a screenshot of this window and send it to Claude.
pause
exit /b 1

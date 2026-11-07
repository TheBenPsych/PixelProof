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

:menu
cls
echo.
echo   =============================================
echo      P I X E L P R O O F
echo   =============================================
echo.
echo     1   Fingerprint a folder of my images
echo     2   Check one page  (paste a link)
echo     3   Check a picture file on this PC
echo     4   Check a list of links  (a .txt file)
echo     5   Show what is in my library
echo     6   Open my reports folder
echo     7   Edit my ban list
echo     8   Check that everything works
echo.
echo     9   Approve a site for one photographer  (licensed use)
echo    10   See everything found so far  (repeat copies, sister sites)
echo    11   Edit my approved sites and sister-site groups
echo     0   Quit
echo.
set "PICK="
set /p "PICK=  Type a number and press Enter: "
if "%PICK%"=="1" goto index
if "%PICK%"=="2" goto page
if "%PICK%"=="3" goto file
if "%PICK%"=="4" goto list
if "%PICK%"=="5" goto status
if "%PICK%"=="6" goto reports
if "%PICK%"=="7" goto ban
if "%PICK%"=="8" goto doctor
if "%PICK%"=="9" goto approve
if "%PICK%"=="10" goto summary
if "%PICK%"=="11" goto lists
if "%PICK%"=="0" exit /b 0
goto menu

:index
echo.
echo   Tip: in File Explorer, right-click the folder and choose "Copy as path"
echo   (on Windows 10, hold Shift while you right-click). Then right-click here to paste.
echo   You can stop any time with Ctrl+C. Run it again later and it carries on.
echo.
set "TARGET="
set /p "TARGET=  Folder: "
if not defined TARGET goto menu
set "TARGET=%TARGET:"=%"
"%VPY%" -m pixelproof index "%TARGET%"
pause
goto menu

:page
echo.
echo   Paste the page link (right-click to paste), then press Enter.
set "TARGET="
set /p "TARGET=  Link: "
if not defined TARGET goto menu
set "TARGET=%TARGET:"=%"
"%VPY%" -m pixelproof check "%TARGET%"
pause
goto menu

:file
echo.
echo   Paste the path of a picture file (right-click the file, "Copy as path").
set "TARGET="
set /p "TARGET=  Picture: "
if not defined TARGET goto menu
set "TARGET=%TARGET:"=%"
"%VPY%" -m pixelproof check "%TARGET%"
pause
goto menu

:list
echo.
echo   Paste the path of a .txt file with one link per line.
set "TARGET="
set /p "TARGET=  List file: "
if not defined TARGET goto menu
set "TARGET=%TARGET:"=%"
"%VPY%" -m pixelproof check "%TARGET%"
pause
goto menu

:status
"%VPY%" -m pixelproof status
pause
goto menu

:reports
"%VPY%" -m pixelproof status >nul
start "" "%~dp0data\reports"
goto menu

:ban
"%VPY%" -m pixelproof status >nul
start "" notepad "%~dp0data\banned.txt"
goto menu

:approve
echo.
"%VPY%" -m pixelproof approve
pause
goto menu

:summary
"%VPY%" -m pixelproof summary
pause
goto menu

:lists
"%VPY%" -m pixelproof status >nul
start "" notepad "%~dp0data\approved.txt"
start "" notepad "%~dp0data\sister-sites.txt"
goto menu

:doctor
"%VPY%" -m pixelproof doctor
pause
goto menu

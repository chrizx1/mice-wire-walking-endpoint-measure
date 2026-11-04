@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo Setting up WireWalk Studio. The first run downloads its Python libraries.
set "TRACKER_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%TRACKER_PY%" goto create
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.12 -m venv .venv
  goto install
)
py -3.13 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.13 -m venv .venv
  goto install
)
echo Python 3.12 or 3.13 is required. Install Python from python.org, then run this again.
pause
exit /b 1
:create
"%TRACKER_PY%" -m venv .venv
if errorlevel 1 goto failed
:install
if not exist ".venv\Scripts\python.exe" goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:run
".venv\Scripts\python.exe" -c "import cv2, numpy, PIL, tkinter"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" wirewalk.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo WireWalk Studio setup or execution failed. The error above describes the problem.
pause
exit /b 1

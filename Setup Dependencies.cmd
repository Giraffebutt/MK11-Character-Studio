@echo off
rem Creates a private Python environment (.venv) for MK11 Character Studio and installs NumPy + Pillow.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.10+ was not found. Install it from https://www.python.org/ ^(tick "tcl/tk"^), then run this again.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" %PY% -m venv .venv
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r app\requirements.txt
if errorlevel 1 (echo Installation failed. & pause & exit /b 1)
echo.
echo Done. Start the studio with "Launch Studio.cmd".
pause

@echo off
rem Create Mod (MK11 Character Studio) - double-click, or drop a mod.json on this file, to build a shared Mod Project.
rem Written without ( ) blocks around paths, so folder names containing brackets work.
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0app"
set "PYEXE="
set "PYARG="
rem 1) private environment made by "Setup Dependencies.cmd"
if exist "%~dp0.venv\Scripts\pythonw.exe" set "PYEXE=%~dp0.venv\Scripts\pythonw.exe"
rem 2) Python on PATH, 3) the Python launcher
if not defined PYEXE for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if defined PYEXE goto :found
for /f "delims=" %%i in ('where pyw 2^>nul') do if not defined PYEXE set "PYEXE=%%i"
if defined PYEXE set "PYARG=-3"
if defined PYEXE goto :found
echo Python 3.10+ was not found. Install it from python.org, then run "Setup Dependencies.cmd".
pause
exit /b 1

:found
"%PYEXE%" %PYARG% -c "import numpy, PIL, tkinter" >nul 2>nul
if not errorlevel 1 goto :start
echo NumPy/Pillow are missing for:
echo   "%PYEXE%"
echo Run "Setup Dependencies.cmd" first.
pause
exit /b 1

:start
start "" "%PYEXE%" %PYARG% create_mod.py %1

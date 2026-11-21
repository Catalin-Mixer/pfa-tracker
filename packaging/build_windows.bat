@echo off
rem Builds PFA-Tax-Tracker.exe for Windows.
rem
rem Needs Python 3.10 or newer from python.org (tick "Add python.exe to PATH" when installing).
rem Copy the whole pfa-tracker folder to Windows, then double-click this file, or run it from a
rem command prompt:   packaging\build_windows.bat
rem The result is dist\PFA-Tax-Tracker.exe in the pfa-tracker folder.

setlocal
cd /d "%~dp0.."

where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")

if not exist ".venv-build\Scripts\python.exe" (
    echo Creating the build environment in .venv-build ...
    %PY% -m venv .venv-build || goto :failed
)
set "VPY=.venv-build\Scripts\python.exe"

echo Installing the packages ...
"%VPY%" -m pip install --upgrade pip || goto :failed
"%VPY%" -m pip install -r requirements.txt PySide6 pyinstaller || goto :failed

echo Building PFA-Tax-Tracker.exe ...
"%VPY%" -m PyInstaller --noconfirm --clean --distpath dist --workpath packaging\build\work packaging\pfa-tracker.spec || goto :failed

echo.
echo Done: %CD%\dist\PFA-Tax-Tracker.exe
echo.
echo Move PFA-Tax-Tracker.exe into the pfa-tracker folder (next to settings.json and data\).
echo It keeps its data next to itself, so the desktop window and the browser version share
echo the same settings, invoices, expenses and proofs.
echo   Desktop window:  double-click PFA-Tax-Tracker.exe
echo   Browser version: PFA-Tax-Tracker.exe --web   (opens http://127.0.0.1:8765)
echo.
pause
exit /b 0

:failed
echo.
echo The build failed; see the messages above.
pause
exit /b 1

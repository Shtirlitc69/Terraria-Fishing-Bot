@echo off
setlocal
set "EXE=%~dp0release\Fishing bot.exe"
set "SRC=%~dp0src\Fishing bot.py"
if exist "%EXE%" (
  start "" "%EXE%"
  exit /b 0
)
where pythonw >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pythonw "%SRC%"
  exit /b 0
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  start "" pythonw "%SRC%"
  exit /b 0
)
echo Fishing bot.exe not found in release\ and pythonw is unavailable.
echo Build with: powershell -ExecutionPolicy Bypass -File scripts\build.ps1
pause

@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem The Windows "py" launcher on this machine starts Python 3.5.
rem server.py needs Python 3.10 or newer.
call :pick
if not defined PYEXE (
  echo This installer needs Python 3.10 or newer.
  echo The py command here is Python 3.5, which cannot run server.py.
  echo Install a current Python from https://www.python.org/downloads/ and try again.
  pause
  exit /b 1
)

"%PYEXE%" server.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" pause
exit /b %RC%

:pick
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PYEXE=python"
  exit /b 0
)

for /d %%D in ("%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3*") do (
  if exist "%%D\python.exe" (
    "%%D\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYEXE=%%D\python.exe"
  )
)
if defined PYEXE exit /b 0

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if exist "%%D\python.exe" (
    "%%D\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYEXE=%%D\python.exe"
  )
)
exit /b 0

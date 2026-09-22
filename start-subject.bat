@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer required'"
  if errorlevel 1 goto missing
  py -3 -m subject01.launch
  goto done
)
where python >nul 2>nul
if errorlevel 1 goto missing
python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11 or newer required'"
if errorlevel 1 goto missing
python -m subject01.launch
goto done
:missing
echo Python 3.11 or newer is required. Install Python, then run this file again.
echo https://www.python.org/downloads/windows/
:done
pause

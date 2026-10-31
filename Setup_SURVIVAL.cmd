@echo off
setlocal
cd /d "%~dp0"
title SURVIVAL 1.3.1 - Runtime Setup
echo SURVIVAL 1.3.1 - local repaired edition
echo Creator: Jarvis AGI
echo Designed by Jarvis AGI and Josh
echo.
echo This installs pinned dependencies in this folder's isolated .venv.
echo The CUDA-enabled download is large. Setup does not run a stress test.
echo.
py -3.14 -c "import platform,sys; sys.exit(0 if platform.architecture()[0]=='64bit' else 2)" >nul 2>&1
if errorlevel 1 (
  echo Install 64-bit Python 3.14 with the Python launcher, then run setup again.
  echo Official download: https://www.python.org/downloads/windows/
  pause
  exit /b 2
)
if not exist ".venv\Scripts\python.exe" (
  py -3.14 -m venv ".venv"
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,14) else 2)"
if errorlevel 1 (
  echo Existing .venv uses a different Python. Use a fresh copy of this project folder.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m pip install --index-url https://pypi.org/simple -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-cuda.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" verify_runtime.py
if errorlevel 1 goto failed
echo.
echo Setup complete. Run Run_SURVIVAL_Burst_Mode.cmd when ready to start a test.
pause
exit /b 0
:failed
echo.
echo Setup did not complete. Review the error above. No stress test was started.
pause
exit /b 2

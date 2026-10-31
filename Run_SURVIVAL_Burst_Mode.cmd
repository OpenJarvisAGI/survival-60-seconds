@echo off
setlocal
cd /d "%~dp0"
title SURVIVAL 1.3.1 - 60 Second Burst
echo SURVIVAL 1.3.1 - local repaired edition
echo Creator: Jarvis AGI
echo Designed by Jarvis AGI and Josh
echo.
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup_SURVIVAL.cmd first. No stress test was started.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" verify_runtime.py
if errorlevel 1 (
  pause
  exit /b 2
)
echo.
echo BETA SOFTWARE - USE AT YOUR OWN RISK.
echo Stress testing can expose instability, cause overheating or crashes,
echo and lose unsaved work. Save your work and close demanding applications.
echo GPU safety stop: 87 C. CPU stop: 95 C only when its sensor is available.
echo CPU temperature may be unavailable; the test warns and may continue.
echo The workload deadline is 60 seconds. Press Ctrl+C to request an abort.
echo.
choice /C YN /N /M "Start the 60-second CPU/RAM/CUDA burst? [Y/N] "
if errorlevel 255 exit /b 2
if errorlevel 2 exit /b 0
if not errorlevel 1 exit /b 0
".venv\Scripts\python.exe" SURVIVAL_60_Seconds.py --seconds 60
set "SURVIVAL_EXIT=%ERRORLEVEL%"
echo.
echo Reports are saved under outputs\SURVIVAL_Test_Records.
pause
exit /b %SURVIVAL_EXIT%

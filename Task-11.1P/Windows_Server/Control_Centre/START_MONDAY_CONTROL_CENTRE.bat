@echo off
setlocal
cd /d C:\MondayServer
if not exist "C:\MondayServer\.venv\Scripts\python.exe" (
  echo Monday Python environment not found at C:\MondayServer\.venv
  pause
  exit /b 1
)
start "Monday Control Centre" "C:\MondayServer\.venv\Scripts\python.exe" "%~dp0control_centre.py"
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8003
endlocal

@echo off
rem Start weather-risk-platform locally, each server in its own window:
rem   API        http://127.0.0.1:8000   (docs at /docs)
rem   dashboard  http://127.0.0.1:5173
rem Close a server's window, or press Ctrl+C in it, to stop that server.
setlocal
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
  echo backend\.venv was not found. Create it first, from this folder:
  echo     py -3.12 -m venv backend\.venv
  pause
  exit /b 1
)

if not exist "frontend\node_modules" (
  echo Installing the dashboard's packages...
  pushd frontend
  call npm install
  if errorlevel 1 ( popd & pause & exit /b 1 )
  popd
)

echo Checking backend packages (installs wind-field from backend\vendor if it is missing)...
"backend\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r backend\requirements.txt
if errorlevel 1 (
  echo Installing the backend requirements failed; see the message above.
  pause
  exit /b 1
)

start "Weather Risk API - 127.0.0.1:8000" /D "%~dp0backend" cmd /k ".venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

rem --strictPort: the API only accepts browser requests from port 5173, so fail
rem loudly if it is taken instead of quietly moving to 5174.
start "Weather Risk dashboard - 127.0.0.1:5173" /D "%~dp0frontend" cmd /k "npm run dev -- --host 127.0.0.1 --port 5173 --strictPort"

echo.
echo   API:        http://127.0.0.1:8000/docs
echo   Dashboard:  http://127.0.0.1:5173
echo.
echo Both servers are starting in their own windows. Close those windows to stop them.
timeout /t 10 >nul

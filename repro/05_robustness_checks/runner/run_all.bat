@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo [%TIME%] [launcher] Read-the-Room strengthening suite launcher
echo [%TIME%] [launcher] Working directory: %CD%

if not exist .venv\Scripts\python.exe (
  echo [%TIME%] [launcher] Creating virtual environment .venv ...
  py -3 -m venv .venv || exit /b 1
) else (
  echo [%TIME%] [launcher] Virtual environment already exists.
)

if not exist .venv\.readroom_requirements_ready goto install_deps
for %%A in (requirements.txt) do set REQTIME=%%~tA
for %%A in (.venv\.readroom_requirements_ready) do set STAMPTIME=%%~tA
rem Timestamp comparison is awkward in cmd; reinstalling is safe and visible only if stamp is absent.
goto deps_done

:install_deps
echo [%TIME%] [launcher] Installing Python dependencies; output is visible ...
.venv\Scripts\python.exe -m pip install --upgrade pip || exit /b 1
.venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
type nul > .venv\.readroom_requirements_ready

:deps_done
echo [%TIME%] [launcher] Starting experiment runner with unbuffered output ...
.venv\Scripts\python.exe -u readroom_strengthen.py %*

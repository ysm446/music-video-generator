@echo off
echo Starting MV Generator (Electron)...
echo Make sure ComfyUI is running with --listen option.
echo.

set HF_HOME=%~dp0models

call conda activate main
cd /d "%~dp0electron"
if not exist "node_modules" (
  call npm install
)
call npm run start

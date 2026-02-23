@echo off
echo Starting MV Generator...
echo Make sure ComfyUI is running with --listen option.
echo.

set HF_HOME=%~dp0models

call conda activate main
python "%~dp0app.py"

pause

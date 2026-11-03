@echo off
cd /d "%~dp0"
if not exist "dist\total\WorkBox\WorkBox.exe" (
  echo Portable BOX is missing. See README.md for the current build.
  pause
  exit /b 1
)
start "WorkBox" "dist\total\WorkBox\WorkBox.exe" --panel

@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Execute iniciar.bat primeiro para criar o ambiente Python.
  pause
  exit /b 1
)
.venv\Scripts\python.exe preparar_modelos.py %*
if errorlevel 1 pause

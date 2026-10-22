@echo off
REM Nuria - verifica chave, internet, microfone e alto-falante antes da apresentacao.
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Execute iniciar.bat primeiro para criar o ambiente Python.
  pause
  exit /b 1
)
.venv\Scripts\python.exe verificar_nuria.py %*
pause

@echo off
REM Nuria - inicia no Windows (cria o ambiente e instala o que faltar).
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Criando ambiente Python na primeira execucao...
  py -3 -m venv .venv || (echo Instale o Python 3.10+ em python.org e tente de novo. & pause & exit /b 1)
  .venv\Scripts\python.exe -m pip install --upgrade pip
)
.venv\Scripts\python.exe -c "import pygame, cv2, numpy, openai, sounddevice, websockets, serial, scipy, aiortc, aiohttp" 2>nul || (
  echo Instalando dependencias...
  .venv\Scripts\python.exe -m pip install -r requirements.txt || (echo Falha ao instalar dependencias. & pause & exit /b 1)
)
if not exist .env (
  copy /y .env.example .env >nul
  echo.
  echo Cole sua chave da OpenAI no arquivo .env que vai abrir, salve e feche o Bloco de Notas.
  echo ^(Para ensaiar sem chave: iniciar.bat --ia simulada^)
  notepad .env
)
REM Usa o python do .venv direto (nao depende do caminho gravado no activate.bat).
.venv\Scripts\python.exe preparar_modelos.py --startup %*
.venv\Scripts\python.exe main.py %*
if errorlevel 1 pause

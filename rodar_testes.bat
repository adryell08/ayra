@echo off
REM Nuria - roda todas as verificacoes automaticas (sem rede, camera ou audio reais).
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Execute iniciar.bat primeiro. & pause & exit /b 1)
set SDL_VIDEODRIVER=dummy
set SDL_AUDIODRIVER=dummy
set FALHOU=0
for %%t in (test_realtime.py test_nuria.py test_api.py test_arduino.py test_operador.py) do (
  echo.
  echo ===== %%t =====
  .venv\Scripts\python.exe %%t --no-png || set FALHOU=1
)
echo.
echo ===== selftest.py =====
.venv\Scripts\python.exe selftest.py --no-png || set FALHOU=1
echo.
if %FALHOU%==0 (echo TODAS AS VERIFICACOES PASSARAM) else (echo ATENCAO: alguma verificacao falhou - veja acima)
pause

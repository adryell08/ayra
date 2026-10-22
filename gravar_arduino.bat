@echo off
REM Nuria - grava o firmware do painel de LEDs no Arduino Uno (uma vez so).
REM Usa o arduino-cli; se nao estiver instalado, instala pelo winget.
chcp 65001 >nul
cd /d "%~dp0"
where arduino-cli >nul 2>nul || (
  echo Instalando o arduino-cli...
  winget install --id ArduinoSA.CLI -e --accept-source-agreements --accept-package-agreements || (
    echo Nao foi possivel instalar. Use a IDE do Arduino: abra arduino\nuria_leds\nuria_leds.ino e clique em Carregar.
    pause & exit /b 1)
  echo Feche e abra este arquivo de novo para o Windows encontrar o arduino-cli.
  pause & exit /b 0
)
arduino-cli core update-index
arduino-cli core install arduino:avr
set PORTA=%1
if "%PORTA%"=="" (
  for /f %%p in ('.venv\Scripts\python.exe -c "import arduino_led as a; p=a.list_ports(); print(p[0][0] if p else '')"') do set PORTA=%%p
)
if "%PORTA%"=="" (
  echo Arduino nao encontrado. Conecte o cabo USB ou informe a porta: gravar_arduino.bat COM4
  pause & exit /b 1
)
echo Gravando na porta %PORTA% (feche a Nuria e o Monitor Serial antes)...
arduino-cli compile --fqbn arduino:avr:uno arduino\nuria_leds || (pause & exit /b 1)
arduino-cli upload -p %PORTA% --fqbn arduino:avr:uno arduino\nuria_leds || (pause & exit /b 1)
echo Pronto! O painel deve mostrar duas "pupilas" acesas. Abra a Nuria e ligue em Ajustes ^> Arduino.
pause

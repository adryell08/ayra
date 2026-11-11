@echo off
REM Ayra - roda todas as verificacoes automaticas (sem rede, camera ou audio reais).
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (echo Execute iniciar.bat primeiro. & pause & exit /b 1)
set SDL_VIDEODRIVER=dummy
set SDL_AUDIODRIVER=dummy
set FALHOU=0
for %%t in (test_realtime.py test_nuria.py test_api.py test_arduino.py test_operador.py test_operador_sem_api.py test_corpo3d.py test_maos_e_tela.py test_gemini.py test_audio.py test_video_operador.py test_uso.py test_historico.py test_conhecimento.py test_memoria.py test_ayrabrain.py test_contexto.py test_rag_texto.py test_lembrar_voz.py test_aceite_pm.py test_avaliacao_rag.py test_visao.py test_eleven.py test_actions.py test_interface.py test_perception.py test_preferences.py test_stability.py test_ferramentas.py test_fila_operador.py test_orquestrador.py test_gesto_expressao.py test_voz.py test_privacidade.py test_painel.py test_reconexao.py test_remoto_falha.py) do (
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

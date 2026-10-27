# Ayra — Arquitetura, requisitos e fluxo de dados

## 1. Objetivo

Aplicação em Python que usa webcam e microfone para capturar o ambiente e a pergunta do usuário, interpreta ambos com um
modelo multimodal da OpenAI e responde por voz, mostrando na interface a imagem usada, a pergunta e a resposta.
Caminho principal: **OpenAI Realtime API** (`gpt-realtime-2.1`, WebSocket), com áudio progressivo nos dois sentidos.

## 2. Arquitetura

| Camada | Módulos | Responsabilidade |
|---|---|---|
| Percepção local | `tracker.py`, `camera_devices.py`, `camera_health.py` | Webcam em thread própria; rostos (YuNet) para o olhar; foto única sob pedido (`capture_still`). Independe da conexão |
| Conversação em tempo real | `assistente_realtime.py`, `realtime.py`, `voz.py`, `credenciais.py` | Sessão Realtime persistente, microfone em blocos, reprodução progressiva, interrupção, reconexão, registro |
| Expressão e interface | `robot.py`, `attention.py`, `gaze.py`, `expressions.py`, `face.py`, `renderer.py`, `painel_assistente.py`, `main.py` | Rosto de LEDs, olhar, emoções, boca, coluna de imagem/pergunta/resposta |
| Modo legado (explícito) | `assistente.py`, `ia.py` | 3 chamadas separadas (transcrição, visão, voz), só com `--ia legado` |

Threads: interface (60 fps, só lê estados) · câmera/detecção · trabalho da pergunta · leitura do WebSocket · envio do WebSocket
(fila única, preserva a ordem) · callback do microfone · callback do alto-falante.

## 3. Fluxo de dados (uma pergunta por voz)

```
CONECTANDO   (só se preciso) WebSocket wss://api.openai.com/v1/realtime?model=… + session.update
             sessão: áudio PCM 24 kHz entrada/saída, voz, instruções (personalidade), ferramenta expressar,
             transcrição da entrada (só para a tela), redução de ruído, turn_detection nulo
OUVINDO      input_audio_buffer.clear; blocos de 40 ms → input_audio_buffer.append enquanto a pessoa fala
             fim: ~0,8 s de silêncio (VAD local adaptativo), Espaço, ou 20 s
CAPTURANDO   foto do quadro atual (JPEG ≤768 px, só em memória)
PENSANDO     input_audio_buffer.commit → conversation.item.create (imagem) → response.create
FALANDO      response.output_audio.delta → alto-falante bloco a bloco; envelope do som tocado → boca
             response.output_audio_transcript.delta → resposta na tela; expressar(...) → rosto
             conversation.item.input_audio_transcription.completed → pergunta na tela (assíncrono)
LIMPEZA      conversation.item.delete da foto da pergunta (não é recobrada nas próximas respostas)
PRONTA       response.done + áudio esgotado; tempos (1º áudio, resposta completa) na tela e em logs/sessoes.jsonl
```

Pergunta digitada: item de texto + item de imagem e `response.create`. Interrupção: `response.cancel`, descarte do
pendente e `conversation.item.truncate(audio_end_ms = amostras tocadas)`. Função sem fala na mesma resposta:
`function_call_output` + novo `response.create`.

## 4. Requisitos

| Requisito | Como é atendido | Verificação (simulada) |
|---|---|---|
| RF01 Capturar imagens por webcam | `CameraSource` + `capture_still` no fim da pergunta | `test_nuria.py` · Foto sob pedido |
| RF02 Reconhecer perguntas pelo microfone | Áudio transmitido em blocos à Realtime; transcrição da entrada para a tela | `test_realtime.py` · voz, microfone 24/48 kHz |
| RF03 Enviar imagem e pergunta a modelo multimodal | Imagem e áudio na mesma sessão Realtime, imagem antes do `response.create` | ordem dos eventos |
| RF04 Exibir resposta e reproduzi-la em áudio | Transcrição progressiva + áudio progressivo | áudio tocado, texto progressivo |
| RF05 Interface com estados | Conectando, Pronta, Ouvindo, Capturando, Pensando, Falando, Erro/Atenção | interface · 3 temas × 3 tamanhos |
| RNF01 Chave fora do código | `.env`/variável de ambiente; nunca na sessão, logs ou tela | configuração da sessão |
| RNF02 Falhas sem encerrar | Sem internet (3 tentativas), queda no meio da fala, sem chave, câmera, microfone, recusa da API → mensagem e segue | reconexão e falhas |
| RNF03 Interface responsiva | Toda rede/áudio fora do laço; estado muda no quadro do clique | app completo |

## 5. Decisões

1. **Realtime por WebSocket** — app Python desktop (WebRTC é para navegador). Sem SDK no caminho principal: só `websockets`,
   eventos JSON documentados (evita depender de versão do SDK).
2. **Fim de fala local** (`turn_detection: null`) — mantém os controles Ouvir/Enviar/Cancelar e garante a imagem antes da resposta.
3. **Meia-duplex** — microfone fechado enquanto ela fala: elimina o eco sem cancelador acústico. Interromper é por Espaço.
4. **Expressões por function calling** — a IA decide a intenção (`expressar(emocao, acao)`); o motor local anima e a fala não lê comandos.
5. **Modo legado explícito** — `--ia legado`; nunca é usado como reserva silenciosa.

## 6. Extensões

- **Aba API** (Ajustes → API): chave colada pela interface e gravada no `.env` (mascarada na tela), teste de
  conexão, troca de modelo/voz; a sessão Realtime se refaz sozinha.
- **Visão pausada** (tecla V): conversa sem enviar imagens; o rastreamento local continua.
- **Painel físico** (Ajustes → Arduino): os mesmos olhos em 32×8 (16×8 por olho) num Arduino Uno + 4× MAX7219,
  pela USB. Detalhes em [`ARDUINO.md`](ARDUINO.md).
- **Controle remoto (etapa 2)**: sala do operador no navegador (aiohttp + aiortc/WebRTC, HTTPS + PIN) com modos
  autônomo/assistido/teleoperado, voz ao vivo, frases, expressões e olhar; voz robótica comum à IA e ao operador.
  Detalhes em [`CONTROLE_REMOTO.md`](CONTROLE_REMOTO.md).
- **Corpo 3D** (F3 ou Ajustes → Corpo 3D): modelo feito em Blender (`blender/nuria_3d.py` → `models/nuria3d.npz`), peças
  rígidas presas a 29 pivôs (cabeça, ombros, cotovelos, punhos, dedos). `gestos.py` descreve cada gesto pela posição do punho,
  direção dos dedos/palma e dobra dos dedos; uma cinemática inversa de dois ossos gera as matrizes. `corpo3d.py` desenha com
  OpenGL 3.3 (moderngl) fora da tela e projeta no visor o mesmo rosto de LEDs do 2D. Gestos chegam da IA (parâmetro `gesto`
  da função `expressar`), da sala do operador e do menu; sem OpenGL, o app fica no 2D e avisa.
- **Câmera ao vivo** na coluna (miniatura ~15 quadros/s, só em memória, só enquanto a tela pede); o envio à IA segue igual.
- **Dedo do meio sem IA**: palma (MediaPipe Palm) → recorte girado → 21 pontos (MediaPipe Hand Landmark, OpenCV Zoo) →
  classificação por distâncias (`gestures.classify_hand`) → confirmação em 2 leituras (`SignReactor`) → rosto bravo e,
  com o corpo 3D, o mesmo gesto de volta. O mesmo caminho reconhece joinha, paz e amor e OK; aceno vira tchau.
- **Gemini Live** (`gemini_live.py`): adaptador que fala o protocolo `BidiGenerateContent` (setup, realtimeInput com
  activityStart/activityEnd, clientContent, toolResponse) e devolve eventos no formato da Realtime; o `RealtimeAssistant`
  não muda. A pergunta falada é guardada e enviada com a foto no pedido de resposta (16 kHz); áudio volta a 24 kHz.
- **Economia**: 1 frase de até ~15 palavras, `max_output_tokens`/`maxOutputTokens` (350 por padrão) e foto de 512 px.
- **Robustez**: cada quadro do laço principal é protegido (erro → `logs/erros.log` + aviso); `faulthandler` grava falhas
  nativas em `logs/falhas.log`; o vídeo do operador lê o último quadro da câmera sem disputar a foto da pergunta.


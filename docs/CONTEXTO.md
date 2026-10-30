# Ayra (projeto Nuria) — Contexto do que foi desenvolvido, testado e melhorado

Atualizado em 29/10/2026. Documento de passagem: resume o estado do projeto, as decisões tomadas e o que ainda falta validar
no computador real. Detalhes de uso ficam no `README.md`; detalhes técnicos, nos arquivos de `docs/`.

---

## 1. O projeto em uma página

- **O que é:** robô virtual em Python. Um rosto de LEDs (ou um corpo 3D) acompanha as pessoas pela webcam e conversa por voz,
  em tempo real, sobre o que está vendo. Projeto 1 de Engenharia de Software (apresentação na semana seguinte a 21/10/2026),
  em disputa com outros projetos: o mais rápido, bonito e eficiente vira a base.
- **Nome:** o robô se chama **Ayra** (desde 27/10/2026); a pasta e vários nomes internos continuam “Nuria”.
- **Destino:** provável base do motor de comunicação de um humanoide num **Raspberry Pi 4**. Por isso leveza, pouca memória
  e fluidez são prioridade máxima.
- **Princípio de produto:** quem está na frente do robô não pode saber se do outro lado está a IA ou um teleoperador
  (mesma voz, nada na tela que revele o operador).

### Pilha técnica
| Área | Tecnologia |
|---|---|
| Interface | Python 3.13, pygame-ce (60 fps; 30 no modo leve) |
| Visão local | OpenCV `cv2.dnn`: YuNet (rostos), MediaPipe Palm + Hand Landmark (mãos/gestos), sorriso, objetos (NanoDet) |
| Conversa | OpenAI Realtime (`gpt-realtime-2.1`, WebSocket) ou Gemini Live (adaptador com o mesmo formato de eventos) |
| Áudio | sounddevice/PortAudio, cancelamento de eco próprio (`audio_duplex`), vocoder de voz robótica (scipy, carga sob demanda) |
| Corpo 3D | modelo feito no Blender (`blender/nuria_3d.py` → `models/nuria3d.npz`), desenhado com moderngl/OpenGL 3.3 em tempo real (o Blender não roda no app) |
| Operador | aiohttp + aiortc (WebRTC), HTTPS com certificado próprio e PIN |
| Painel físico | Arduino Uno + 4× MAX7219 (32×8) pela USB |
| Memória | SQLite (WAL + FTS5) em `logs/memoria.db` |

---

## 2. Regras combinadas (valem até segunda ordem)

- **Não subir nada para o GitHub** (`adryell08/ayra`, privado, branch `main-tele`). Só o Adryell publica.
- **Como os olhos foram feitos é confidencial.** O repositório do grupo (`joaodorast/projeto-visao-llm`) é público.
- A chave da OpenAI fica no `.env` e não é trocada nem pedida; chaves nunca aparecem por inteiro (`sk-…XXXX`, `AIza…XXXX`).
- Nunca versionar `.env`, `*.local.json`, `logs/`, `certs/`, `*.db`.
- Os `*.local.json` do ambiente de desenvolvimento têm valores de teste e **não** vão para o PC.
- Mudanças cirúrgicas, comunicação direta em português, raciocínio interno fora dos arquivos entregues.

---

## 3. O que existe hoje (por área)

### 3.1 Conversa em tempo real
- Sessão persistente (aberta ao iniciar o app e reaproveitada); áudio enviado em blocos de 40 ms enquanto a pessoa fala;
  fim de fala local (~0,8 s de silêncio); a foto do momento entra antes do pedido de resposta; a fala começa enquanto é gerada.
- Interrupção (barge-in): Espaço ou falar por cima cancela a resposta e corta o histórico no trecho ouvido.
- Expressões e gestos por *function calling* (`expressar(emocao, acao, gesto)`); a fala nunca lê comandos.
- Reconexão com até 3 tentativas, levando as últimas trocas como contexto. Modelo sem `reasoning` é tratado sozinho.
- **Gemini Live** como alternativa, trocável sem reiniciar.
- **Economia:** respostas de 1 frase (~15 palavras), teto de tokens configurável (350 por padrão), foto de 512 px apagada da sessão após a resposta.
- **Conversa contínua:** arrastar o botão de falar para cima (ou L) trava o microfone, como no WhatsApp.

### 3.2 Áudio (onde mais se trabalhou)
- Um microfone só, repartido entre IA (24 kHz) e operador (48 kHz), com **cancelamento de eco** (o que o alto-falante toca é tirado do microfone).
- **Atraso de ~0,8 s até o operador** — corrigido (verificado com sinais simulados; falta medir no PC, item U6): fila do microfone para o operador limitada a 60 ms (descarta o atraso velho), envio sem saltos de thread.
- **Voz do operador travando/picotando** — corrigido (verificado com sinais simulados; falta confirmar, itens U5 e U9): reamostragem com estado, *buffer de jitter* adaptativo no alto-falante
  (120 ms, cresce até 400 ms se faltar áudio e volta a encolher), blocos de 40 ms, processamento de eco e vocoder fora das
  threads de áudio, troca de thread do Python a cada 1 ms.
- **Voz robótica com grau 0–100 %** (como nos filtros do Animaze), igual para IA e operador, menos ecoada (voz original
  alinhada no tempo com a robótica). Ajuste em Ajustes ou no controle deslizante da sala do operador.

### 3.3 Controle remoto (etapa 2)
- Sala do operador no navegador (rede local, HTTPS + PIN, um operador por vez, 10 PINs errados bloqueiam).
- Modos **Autônomo**, **Assistido** e **Teleoperado**; voz ao vivo (segurar ou travar), frase digitada, expressões, 13 gestos,
  olhar clicando no vídeo, corpo 3D, visão da IA, voz robótica, latência.
- Vídeo a **10 quadros/s** (5 ou 15 disponíveis). Página adaptada ao celular.
- Nada na tela do robô revela o operador (o selo “Operador conectado” é opcional e pode ser desligado).
- Internet: mesma página por VPN (ex.: Tailscale), sem mudar código.

### 3.4 Rosto, corpo e percepção
- Rosto 2D de LEDs com olhar, piscadas e emoções; **corpo 3D** com os mesmos olhos no visor, cabeça seguindo o rosto e 13 gestos
  (tchau, joia, dedo do meio, apontar, paz e amor, OK, aplaudir, encolher ombros, comemorar, continência, pensar, sim, não).
- Gestos vistos pela câmera **sem IA** (aceno, joinha, paz e amor, OK, dedo do meio) com resposta do robô.
- Câmera ao vivo na coluna (só na tela, nada gravado).
- **Painel LED físico** 32×8 com calibração de montagem pelo menu.

### 3.5 Interface
- **Menu de Ajustes reagrupado**: Reações e rosto · Corpo e gestos · Conversa e IA · Conexões, com seções; rolagem por roda,
  arraste, barra e PgUp/PgDn.
- **Dock** com 5 ícones: câmera, aparência, movimento, **conversas** (balão) e ajustes.
- **Modo de uso**: só o rosto, o botão de falar e a conversa. Sai com F10 ou segurando 1,2 s o canto superior direito.
- **Conversas** em tela própria, estilo WhatsApp/ChatGPT: lista com busca à esquerda, balões à direita, Retomar, Apagar (com
  confirmação), Nova conversa; aba **Memória** ao lado.
- **Repetir resposta** (seta circular): toca de novo o áudio da última resposta, sem nova chamada à IA.
- **“Nuria, encerrar conversa”** por voz: cancela a resposta e solta o microfone (frases soltas como “para” não valem).

### 3.6 Memória, contexto e aprendizado (MVP da especificação de 29/10)
- SQLite local; escrita numa thread própria (nunca no caminho de voz); se o banco falhar, a memória se desliga e a conversa segue.
- Aprende por **frases explícitas**, sem LLM extra: “lembre-se de que…”, “a partir de agora…”, “sempre/nunca…” → instrução;
  “meu nome é…”, “eu gosto de…” → preferência **não verificada**; “esqueça isso/tudo”, “esqueça a instrução sobre X”, “pare de…” → revoga.
- A IA recebe só um texto curto (≤ 1100 caracteres): instruções vigentes e preferências em sessão nova; **resumo do que o
  operador fez** antes da primeira resposta depois que ele devolve o controle; mudanças feitas no painel, na hora.
- Separação por usuário (`user_id`); preferência só vira conhecimento global com validação de um revisor.
- **Gestão sem operador:** aba Memória na tela Conversas (revogar/apagar) e página **`/memoria`** no navegador com o PIN da sala
  (nova instrução, editar, revogar, validar, buscar, métricas, esquecer tudo). Liga/desliga e “Esquecer tudo” em Ajustes;
  `--sem-memoria` desliga. Retenção de eventos: 90 dias.
- O áudio do operador **não é transcrito**: registra-se quando ele falou e por quanto tempo; o que ele digita entra com texto.
- Detalhes, limites e rollback: `docs/MEMORIA.md`.

### 3.7 Modo leve (Raspberry Pi 4)
- `--leve` (automático em ARM ou com `NURIA_LEVE=1`): 30 fps, sem brilho nem vidro desfocado, detecção em 320 px,
  rosto detectado 10×/s, 3D com qualidade adaptativa.

---

## 4. Desempenho: o que se mediu e o que mudou

| Momento | Sintoma (log real do PC, Intel Iris Xe) | O que foi feito |
|---|---|---|
| Início da investigação | App “lento, com bug e travando” ao conectar IA ou operador | Log `logs/desempenho.log` a cada 10 s: fps, tempo por quadro (p50/p95/máx), tempo e degrau do 3D, CPU do processo e tempo por etapa do quadro |
| 1º log | 3D caro em placa integrada | Qualidade adaptativa do 3D (MSAA/escala em 4 degraus); visor redesenhado só quando o 3D redesenha |
| Reclamação de pixelado | O 3D adaptativo baixava a resolução e não resolvia | Qualidade adaptativa **só no modo leve**; no PC, qualidade cheia de volta |
| 2º log | 43 fps → ~6 fps em ~2 min, **sem operador**, CPU 125 % → 320 %, todas as etapas inflando | Diagnóstico: as threads de visão disputavam a CPU. Rosto detectado no máximo 15×/s (10 no leve); mãos 5,5 Hz, sorriso 3 Hz, objetos 1,7 Hz (antes 8, 6,7 e 2); threads de visão com **prioridade abaixo do normal** |
| 3º log | “Melhorou muito”, mas ainda oscila (35 fps → 5–12 fps, CPU 250–330 %) | OpenCV e bibliotecas numéricas limitadas a **2 threads por chamada** em todos os modos; o log passou a mostrar a **CPU por thread** (precisa `pip install psutil` no `.venv`) |

**Pendente:** novo `logs/desempenho.log` de ~5 min com o 3D ligado, já com `psutil`, para apontar a thread que ainda consome CPU.

---

## 5. Testes automáticos (estado atual)

Tudo com rede, microfone, alto-falante, câmera e porta serial **simulados** — verificam lógica, ordem de eventos e falhas,
não substituem o teste no computador real. `rodar_testes.bat` roda todas as suítes.

| Suíte | Verificações |
|---|---|
| `test_realtime.py` (caminho Realtime) | 67/67 |
| `test_nuria.py` (interface, câmera, falhas, legado) | 81/81 |
| `test_api.py` (chave e modelo pela interface) | 23/23 |
| `test_arduino.py` (painel LED) | 38/38 |
| `test_operador.py` (sala do operador, WebRTC real local) | 37/37 |
| `test_corpo3d.py` (corpo 3D e gestos) | 50/50 |
| `test_maos_e_tela.py` (câmera ao vivo, celular, gestos vistos) | 39/39 |
| `test_gemini.py` (Gemini Live, economia, estabilidade) | 41/41 |
| `test_audio.py` (eco, conversa contínua, ligação) | 37/37 |
| `test_uso.py` (voz com grau, Modo de uso, menu, fila de áudio, modo leve, carga da visão) | 54/54 |
| `test_historico.py` (conversas, repetir, encerrar por voz, dock) | 42/42 |
| `test_memoria.py` (memória, modos, painel `/memoria`, aba no robô, falhas) | 60/60 |
| `selftest.py` (rosto, olhar, percepção) | 255/255 |
| `test_actions`, `test_interface`, `test_perception`, `test_preferences`, `test_stability` | passam (saída 0) |

**Total contado: 824 verificações.** Observação: um teste de tempo do Modo de uso (F10) falhou uma vez sob carga e passou nas
três repetições seguintes.

Alguns números medidos nos testes simulados: gravar 500 eventos de memória ≈ 2 ms; montar o contexto da memória ≈ 1 ms;
desenhar a aba Memória ≈ 4 ms por quadro (sem GPU); latência por turno com memória ligada ≈ desligada.

---

## 6. Validação real que falta (no PC e no Raspberry)

Roteiro completo, com campos para preencher, em `docs/REGISTRO_TESTES.md`, seção 2. Prioridades antes da apresentação:

1. **Desempenho:** 5 min com corpo 3D (U11) e com o painel de memória aberto no celular (M8).
2. **Áudio da ligação:** atraso (U6), voz do operador contínua (U5, U9), grau da voz robótica (U8).
3. **Privacidade do teleoperado:** nada na tela revela o operador (U10).
4. **Conversas e memória:** U12–U15 e M1–M9.
5. **Raspberry Pi 4:** `--leve` a 30 fps com operador conectado (U7).
6. Demonstração e falhas de rede/chave/câmera (seções 2.1 e 2.2).

---

## 7. Decisões em aberto

- **Áudio em processo separado** (sugerido): tiraria o áudio da disputa com a interface no Python. Para o humanoide, um núcleo
  de áudio em Rust ou C++ é o caminho de longo prazo. Aguarda decisão.
- **Comparativo do “GPT Astra”** com `projeto-visao-llm`: já incorporados histórico, repetir, encerrar por voz e testes no
  `rodar_testes.bat`. Ficaram de fora por ora: diagnóstico integrado numa tela única e reorganização do código em pacotes.
- **Memória, fase 2:** embeddings, consolidação de memórias, MCP, PostgreSQL, vários usuários com login, transcrição do áudio do operador.

---

## 8. Onde está cada coisa

| Arquivo | Papel |
|---|---|
| `main.py` | Laço da interface, opções de linha de comando, menu, sala do operador, memória e log de desempenho |
| `assistente_realtime.py`, `realtime.py`, `gemini_live.py` | Conversa em tempo real (OpenAI e Gemini), memória ligada à sessão |
| `assistente.py`, `ia.py` | Estados compartilhados, conversas (id, nova, retomar), modo legado |
| `voz.py`, `audio_duplex.py`, `filtro_voz.py` | Microfone, alto-falante com jitter buffer, eco, voz robótica |
| `operador.py`, `operador_web/index.html`, `operador_web/memoria.html` | Sala do operador e painel de memória |
| `memoria.py` | Memória SQLite: eventos, instruções, preferências, busca, contexto, retenção |
| `historico.py`, `conversas_ui.py` | Histórico de conversas e a tela Conversas/Memória |
| `menu.py`, `renderer.py`, `painel_assistente.py`, `theme.py` | Menu, dock, desenho, coluna da conversa, temas |
| `tracker.py`, `auxiliary.py`, `perception.py`, `gestures.py` | Câmera, detecção (com limite de taxa e prioridade baixa), gestos vistos |
| `robot.py`, `face.py`, `gaze.py`, `expressions.py`, `attention.py` | Rosto, olhar e expressões |
| `corpo3d.py`, `gestos.py`, `blender/` | Corpo 3D e gestos |
| `arduino_led.py`, `arduino/` | Painel LED |
| `credenciais.py`, `entrada_chave.py` | Chaves e configuração (`.env`) |
| `docs/` | `ARQUITETURA.md`, `CONTROLE_REMOTO.md`, `ARDUINO.md`, `MEMORIA.md`, `REGISTRO_TESTES.md`, este `CONTEXTO.md` |
| `logs/` (fora do Git) | `sessoes.jsonl` (conversas), `memoria.db`, `desempenho.log`, `erros.log`, `falhas.log` |

### Opções úteis
`iniciar.bat --ia simulada` (ensaio sem rede) · `--uso` (Modo de uso) · `--leve` (Raspberry) · `--fullscreen` ·
`--sem-registro` (não grava conversas nem memória) · `--sem-memoria` · `--fps N` · `--camera N`.

# Ayra — Visão e Interpretação do Ambiente com LLM

Robô virtual em Python: um rosto de LEDs que acompanha as pessoas pela webcam e **responde por voz, em tempo real, perguntas sobre o que está vendo**.
O caminho principal usa uma sessão persistente da **OpenAI Realtime API** (`gpt-realtime-2.1`): o áudio da pergunta é transmitido
enquanto você fala, a foto do momento entra na mesma sessão e a resposta já começa a tocar enquanto é gerada.

> Projeto 1 — Engenharia de Software. Protótipo de estudantes.

## Executar (Windows)

1. Instale o **Python 3.10+** (python.org, marque “Add to PATH”).
2. Dê dois cliques em **`iniciar.bat`**. Ele instala o que faltar (inclusive o novo pacote `websockets`) e, se não houver `.env`, abre um para a chave.
   Se a chave já está no `.env`, nada é pedido.
3. Antes da apresentação: **`verificar_nuria.bat`** conecta de verdade na Realtime, mede o tempo até o primeiro áudio e testa microfone e webcam.

```bat
iniciar.bat                         :: Realtime (padrão)
iniciar.bat --ia simulada           :: servidor Realtime simulado: ensaio sem chave e sem internet
iniciar.bat --ia legado             :: modo antigo, explícito: transcrição → visão → voz em 3 chamadas
iniciar.bat --camera 1              :: fixar a webcam
iniciar.bat --sim --imagem mesa.jpg :: sem webcam: usa uma foto
iniciar.bat --fullscreen            :: tela cheia (F11 alterna)
iniciar.bat --sem-registro          :: não grava logs/sessoes.jsonl
```

## Como usar

| Ação | Como |
|---|---|
| Perguntar por voz | **Espaço** ou **Clique para falar**. Fale; ela envia sozinha após ~0,8 s de silêncio (ou aperte de novo). |
| Digitar a pergunta | **Enter** ou botão de teclado; Enter envia, Esc cancela. |
| Interromper | **Espaço** durante a resposta: ela para na hora e volta a ouvir. **Esc** só cancela. |
| Pausar a visão | **V** ou botão da câmera: só conversa, nenhuma imagem é enviada (o olhar segue rostos normalmente). De novo retoma. |
| Corpo 3D | **F3**: alterna rosto 2D / corpo 3D com gestos. |
| Pausar câmera | **P**. Ajuda completa: **F1**. |

Estados (texto + forma): **Conectando · Pronta · Ouvindo · Capturando imagem · Pensando · Falando · Erro/Atenção**. A conexão é aberta ao iniciar o app
e reaproveitada entre perguntas; o subtítulo da coluna mostra se está conectada.

## Controle remoto (etapa 2)

Um operador em outra sala abre a **sala do operador** no navegador (rede local, HTTPS + PIN), vê e ouve o robô e alterna
entre **Autônomo**, **Assistido** e **Teleoperado**; pode falar pela Ayra (voz ao vivo ou frase digitada), disparar
expressões e direcionar o olhar clicando no vídeo. **Voz robótica** opcional deixa a IA e o operador com a mesma voz.
Ligue em **Ajustes → Controle remoto**. Detalhes: [`docs/CONTROLE_REMOTO.md`](docs/CONTROLE_REMOTO.md).

## Corpo 3D e gestos

**F3** (ou **Ajustes → Corpo 3D**) troca o rosto 2D pelo corpo 3D modelado no Blender, com os **mesmos olhos** no visor.
A cabeça acompanha o rosto rastreado e os braços fazem **13 gestos**: tchau, joia, dedo do meio, apontar, paz e amor, OK,
aplaudir, encolher ombros, comemorar, continência, pensar, sim e não. A IA escolhe gestos sozinha (função `expressar`)
e o operador tem um botão para cada um. Precisa de OpenGL 3.3 (qualquer placa de vídeo dos últimos ~10 anos); sem isso,
o app fica no 2D e explica o motivo. Para alterar o modelo: abra `blender/nuria_3d.py` no Blender, ajuste e rode com
`EXPORTAR = True` (Blender 4.1+; gera `nuria3d.npz`, que vai para a pasta `models/`).

## Câmera ao vivo e gestos de volta (sem IA)

- A coluna mostra a **câmera ao vivo** (miniatura só na tela, nada gravado). A IA continua recebendo **uma foto por pergunta**,
  que aparece no canto da miniatura com o selo “Enviada”.
- O OpenCV reconhece gestos pela webcam (21 pontos da mão, modelo do OpenCV Zoo baixado por `preparar_modelos.bat`), **sem IA**,
  e a Ayra responde: **aceno → tchau**, **joinha → joia**, **paz e amor → paz**, **OK → OK** (fica feliz) e **dedo do meio → dedo
  do meio** (fica brava). Com o corpo 3D ligado ela devolve o gesto com as mãos. Liga/desliga em **Reações → Responder gestos**
  e **Devolver dedo do meio**.

## Gemini Live (alternativa à Realtime)

**Ajustes → API → Provedor da conversa** alterna entre **OpenAI Realtime** e **Gemini Live** (Google) sem reiniciar. A chave
do Gemini (Google AI Studio, começa com `AIza`) é colada na mesma aba e gravada no `.env` como `GEMINI_API_KEY`. O fluxo é o
mesmo nos dois: fim de fala local, foto junto com a pergunta, fala em streaming, interrupção, expressões, gestos e operador.
`iniciar.bat --ia simulada` ensaia o provedor escolhido sem rede.

## Economia de tokens

Respostas de **1 frase (até ~15 palavras)**, teto por resposta (**Ajustes → API → Tamanho das respostas**: curtíssimas 220,
curtas 350 [padrão, ~16 s de fala], normais 700 ou sem limite) e foto de **512 px** por pergunta (`NURIA_IMAGEM_LADO`).
A foto sai da sessão depois da resposta (Realtime). Silêncio ou cancelamento não enviam nada.

## Se o app fechar

Erros inesperados num quadro não fecham mais o app: vão para `logs/erros.log` com um aviso na tela. Falhas nativas (driver de
vídeo, áudio, codec) ficam em `logs/falhas.log`, e o `iniciar.bat` segura a janela se o app sair com erro.

## Painel de LEDs (Arduino)

Olhos espelhados num painel físico 32×8 (Arduino Uno + 4× MAX7219). Ligações: **VCC→5V, GND→GND, DIN→D11, CS→D10, CLK→D13**.
Grave o firmware com `gravar_arduino.bat` (ou pela IDE do Arduino) e ligue em **Ajustes → Arduino**.
Passo a passo, calibração e alimentação: [`docs/ARDUINO.md`](docs/ARDUINO.md).

## Como o tempo real funciona

```
Espaço ─► microfone 24 kHz em blocos de 40 ms ──► input_audio_buffer.append   (enquanto você fala)
fim da fala (silêncio local ou Espaço) ─► foto da webcam
          ─► input_audio_buffer.commit ─► conversation.item.create (imagem) ─► response.create
                                     (mesma fila de envio: a imagem sempre chega antes do pedido de resposta)
servidor ─► response.output_audio.delta ─► alto-falante bloco a bloco; boca segue o volume do som tocado
         ─► response.output_audio_transcript.delta ─► resposta aparece escrita enquanto fala
         ─► expressar(emocao, acao) (function calling) ─► rosto chora, pisca, fica bravo… sem ler comandos
         ─► conversation.item.input_audio_transcription.completed ─► pergunta escrita na tela (não atrasa a fala)
Fim da resposta ─► conversation.item.delete da foto (cada imagem é cobrada uma vez; a conversa em texto/áudio fica)
Interromper ─► response.cancel + descarta áudio pendente + conversation.item.truncate (só o trecho ouvido fica no histórico)
```

- **Eco:** o microfone só fica aberto enquanto ela **não** fala (e espera 0,2 s após uma interrupção), então ela não ouve a própria voz.
- **Fim da fala local** (`turn_detection: null`): os botões Ouvir/Enviar/Cancelar continuam valendo e a imagem entra antes da resposta.
- **Queda de conexão:** em repouso, reconecta na próxima pergunta (até 3 tentativas) e leva as últimas trocas como contexto. No meio de uma resposta, mostra o erro e segue.
- **Configuração:** mudanças seguras no `.env` viram `session.update`; trocar modelo, voz ou chave recria a sessão.

## Arquitetura

| Módulo | Papel |
|---|---|
| `main.py` | Laço da interface (60 fps), teclado/mouse, `--ia realtime|simulada|legado` |
| `assistente_realtime.py` | Caminho principal: sessão Realtime, estados, imagem, interrupção, expressões, reconexão |
| `realtime.py` | WebSocket (threads de leitura e envio), servidor Realtime simulado, utilitários de PCM |
| `voz.py` | `MicStream` (24 kHz em blocos, fim da fala) e `StreamPlayer` (reprodução progressiva, contagem do que tocou) |
| `assistente.py`, `ia.py` | Estados/rosto compartilhados, personalidade; modo legado (3 chamadas) só com `--ia legado` |
| `credenciais.py` | Lê a chave do ambiente ou do `.env` (fora do Git); nunca a exibe |
| `painel_assistente.py` | Coluna: estado, imagem usada, pergunta, resposta, botões |
| `tracker.py` + rosto (`robot.py`, `attention.py`, `gaze.py`, `expressions.py`, `face.py`, `renderer.py`) | Rastreamento local e animações, independentes da conexão |

Detalhes: [`docs/ARQUITETURA.md`](docs/ARQUITETURA.md) · Testes: [`docs/REGISTRO_TESTES.md`](docs/REGISTRO_TESTES.md).

## Chave e modelo pela interface

**Ajustes (F10) → API**: provedor (OpenAI Realtime ou Gemini Live), colar as chaves (Ctrl+V; na tela só aparecem os 4 últimos
caracteres), **Testar conexão**, trocar **Modelo** e **Voz** do provedor escolhido, **Tamanho das respostas** ou **Remover chave**. Tudo é gravado no `.env`
(fora do Git) e a sessão Realtime se refaz sozinha, sem reiniciar. Uma variável `OPENAI_API_KEY` do Windows tem prioridade sobre o `.env` (a aba avisa).

## Configuração (`.env`, todas opcionais exceto a chave)

| Variável | Padrão | Observação |
|---|---|---|
| `OPENAI_API_KEY` | — | Já configurada; variável de ambiente do sistema tem prioridade |
| `NURIA_MODELO_REALTIME` | `gpt-realtime-2.1` | Recria a sessão ao mudar |
| `NURIA_VOZ_REALTIME` | `marin` | alloy, ash, ballad, coral, echo, sage, shimmer, verse, marin, cedar. Recria a sessão |
| `NURIA_TRANSCRICAO` | `gpt-4o-mini-transcribe` | Só para mostrar a pergunta; `desligada` desativa |
| `NURIA_RUIDO` | `far_field` | `near_field` (fone), `far_field` (notebook), `desligado` |
| `NURIA_RACIOCINIO` | `low` | `minimal`/`low`/`medium`/`high`; vazio usa o padrão do modelo. Se o modelo recusar, é retirado sozinho |
| `NURIA_TIMEOUT` | `30` | Segundos para conectar/aguardar |
| `NURIA_PROVEDOR` | `openai` | `openai` (Realtime) ou `gemini` (Gemini Live) |
| `GEMINI_API_KEY` | — | Chave do Gemini (Google AI Studio); `GOOGLE_API_KEY` também vale |
| `NURIA_MODELO_GEMINI` | `gemini-3.8-live` | ou `gemini-3.1-flash-live-preview` |
| `NURIA_VOZ_GEMINI` | `Kore` | Kore, Aoede, Leda, Zephyr, Puck, Charon, Fenrir, Orus |
| `NURIA_MAX_TOKENS` | `350` | Teto por resposta (áudio conta; ~20–25 tokens por segundo de fala); `0` = sem limite |
| `NURIA_IMAGEM_LADO` | `512` | Maior lado da foto enviada; 768 = mais detalhe e mais tokens |
| `NURIA_MODELO_VISAO`, `_STT`, `_TTS`, `NURIA_VOZ` | — | Só o modo legado |

## Privacidade

- A chave nunca entra no código, no Git nem na tela.
- Nada de vídeo contínuo: **uma foto** por pergunta (768 px, JPEG), só em memória, mostrada na coluna.
- Áudio só sai do computador enquanto o estado é **Ouvindo**; nada de áudio ou imagem é gravado em disco.
- `logs/sessoes.jsonl` guarda só texto e tempos; desligue com `--sem-registro`.

## Estrutura do repositório

```
main.py                     laço da interface e opções de linha de comando
assistente_realtime.py      conversa em tempo real (estados, imagem, interrupção, expressões, reconexão)
realtime.py                 WebSocket da Realtime + servidor simulado
gemini_live.py              Gemini Live por trás da mesma interface + servidor Gemini simulado
voz.py                      microfone em blocos e reprodução progressiva
credenciais.py              chave fora do código (.env / variável de ambiente)
ia.py, assistente.py        personalidade, estados compartilhados e modo legado (3 chamadas)
painel_assistente.py        coluna: imagem, pergunta, resposta, botões
entrada_chave.py            folha para colar a chave (aba API)
tracker.py, robot.py, …     câmera, rastreamento, olhar, expressões e desenho do rosto
arduino_led.py              painel LED 32×8 (render, mapeamento, serial)
operador.py, operador_web/  sala do operador: servidor HTTPS + WebRTC e a página do navegador
filtro_voz.py               voz robótica (vocoder) e voz local gratuita
corpo3d.py, gestos.py       corpo 3D (OpenGL) e gestos (esqueleto + cinemática inversa)
blender/nuria_3d.py         modelo 3D no Blender (gera models/nuria3d.npz)
arduino/nuria_leds/         firmware do Arduino Uno
docs/                       ARQUITETURA.md · REGISTRO_TESTES.md · ARDUINO.md · CONTROLE_REMOTO.md
test_*.py, selftest.py      testes automáticos (rodar_testes.bat roda todos)
```

## Testes

`rodar_testes.bat` roda todas as suítes no Windows. Individualmente:

```bash
python test_realtime.py     # 67 verificações do caminho Realtime, com conexão e dispositivos SIMULADOS
python test_nuria.py        # 81 verificações: interface, câmera, falhas e modo legado
python test_api.py         # 23 verificações da aba API (chave pela interface)
python test_operador.py    # 37 verificações do controle remoto e da voz robótica (WebRTC real em 127.0.0.1)
python test_arduino.py     # 38 verificações do painel LED (porta serial simulada)
python test_corpo3d.py     # 44 verificações do corpo 3D: modelo, gestos, OpenGL, IA, operador e menu
python test_maos_e_tela.py # 39 verificações: nome, câmera ao vivo, operador no celular e gestos vistos
python test_gemini.py      # 41 verificações: Gemini Live (simulado), economia de tokens e estabilidade
python selftest.py --no-png # 255 verificações do rosto/percepção
```

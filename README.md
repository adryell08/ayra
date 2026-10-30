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

## Modo de uso (só interação)

`iniciar.bat --uso` ou **Ajustes → Modo de uso**. Ficam só o rosto (ou corpo 3D), o botão grande de falar (arraste para cima
para travar o microfone) e a conversa. Sem ajustes, diagnóstico, ajuda, dock nem painéis. Fica apenas um ponto de câmera
(verde = ativa) e, se houver operador, o aviso de que ele está conectado. **Sair:** F10, ou segurar 1,2 s o canto superior
direito da tela (serve em tela de toque). Nesse modo **Esc** só interrompe a resposta; nunca fecha o app.

## Modo leve (Raspberry Pi 4)

`iniciar.bat --leve` (liga sozinho em processador ARM ou com `NURIA_LEVE=1`): 30 quadros/s, sem brilho nem vidro desfocado,
detecção em 320 px e OpenCV com 2 threads. Medido com o operador conectado (simulado, 2 núcleos): CPU de ~45 % para ~26 %,
thread principal de 28 % para 10 %. `--fps N` continua valendo. A memória sobe ~100 MB ao conectar o operador (WebRTC) e
estabiliza (sem vazamento em 80 s); o pacote de sinais (`scipy`, ~70 MB) só carrega quando a voz robótica é usada.

## Controle remoto (etapa 2)

Um operador em outra sala abre a **sala do operador** no navegador (rede local, HTTPS + PIN), vê e ouve o robô e alterna
entre **Autônomo**, **Assistido** e **Teleoperado**; pode falar pela Ayra (voz ao vivo ou frase digitada), disparar
expressões e direcionar o olhar clicando no vídeo. **Voz robótica** opcional deixa a IA e o operador com a mesma voz.
Ligue em **Ajustes → Conexões**. Detalhes: [`docs/CONTROLE_REMOTO.md`](docs/CONTROLE_REMOTO.md).

## Corpo 3D e gestos

**F3** (ou **Ajustes → Corpo e gestos**) troca o rosto 2D pelo corpo 3D modelado no Blender, com os **mesmos olhos** no visor.
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

## Conversa contínua e ligação com o operador

- **Arraste “Clique para falar” para cima** (ou tecla **L**): o microfone trava, como no WhatsApp. Com a IA, a conversa fica
  contínua: fale quando quiser, ela responde sem apertar nada e **para se você falar por cima**. Toque no botão de novo para encerrar.
- **Teleoperado**: a pessoa e o operador falam ao mesmo tempo, como numa ligação. Na sala do operador, **deslize “Segure para
  falar” para cima** (ou tecla **T**) para deixar o microfone aberto; a voz dele só sai pelo robô quando ele fala de fato.
- Vídeo para o operador: **10 quadros/s** (5, 10 ou 15 em **Ajustes → Conexões → Vídeo**). A fila de áudio do robô para o operador guarda no máximo 60 ms, então o atraso não se acumula.

## Conversas salvas, repetir e encerrar por voz
- **Ícone de balão no dock** (entre Movimento e Ajustes) ou **Ajustes → Conversas** abre uma tela como WhatsApp/ChatGPT: à esquerda a busca (digite e a lista filtra por pergunta ou
  resposta) e as conversas, da mais recente; à direita a conversa em balões (você à direita, o robô à esquerda). **Retomar**
  (ou Enter) leva as últimas trocas como texto para uma sessão nova do provedor; **Apagar** pede um segundo toque; **Nova conversa**
  começa do zero. Setas trocam de conversa, Esc limpa a busca e depois fecha. Só texto e tempos ficam em `logs/sessoes.jsonl`
  (com `--sem-registro` nada é gravado, nem aparece aqui).
- **Botão de repetir** (seta circular, ao lado do olho): toca de novo o áudio da última resposta, sem nova foto nem nova chamada à IA.
- **“Nuria, encerrar conversa”** (também “terminar/finalizar a conversa/escuta/ligação”): cancela a resposta e solta o microfone
  travado. Palavras soltas como “para” não valem, e no modo teleoperado o comando é ignorado.

## Memória entre conversas
O Nuria guarda **instruções vigentes** ("lembre-se de que a sala é a 12", "a partir de agora fale devagar") e **preferências**
("meu nome é…") em `logs/memoria.db`, e as leva para a IA quando abre uma conversa nova, volta de uma queda ou o operador devolve o controle
(com um resumo do que ele fez). O operador pode registrar instruções na sala dele. "Esqueça isso" revoga.
Ligar/desligar e **Esquecer tudo** em Ajustes → Conversa e IA; `--sem-memoria` desliga.
**Gestão**: aba **Memória** na tela Conversas (revogar/apagar) e o painel **`/memoria`** no navegador, com o PIN da sala, sem precisar de
operador (editar, validar, buscar, métricas). Detalhes, limites e rollback em `docs/MEMORIA.md`.

## Gemini Live (alternativa à Realtime)

**Ajustes → Conversa e IA → Provedor da conversa** alterna entre **OpenAI Realtime** e **Gemini Live** (Google) sem reiniciar. A chave
do Gemini (Google AI Studio, começa com `AIza`) é colada na mesma aba e gravada no `.env` como `GEMINI_API_KEY`. O fluxo é o
mesmo nos dois: fim de fala local, foto junto com a pergunta, fala em streaming, interrupção, expressões, gestos e operador.
`iniciar.bat --ia simulada` ensaia o provedor escolhido sem rede.

## Economia de tokens

Respostas de **1 frase (até ~15 palavras)**, teto por resposta (**Ajustes → Conversa e IA → Tamanho das respostas**: curtíssimas 220,
curtas 350 [padrão, ~16 s de fala], normais 700 ou sem limite) e foto de **512 px** por pergunta (`NURIA_IMAGEM_LADO`).
A foto sai da sessão depois da resposta (Realtime). Silêncio ou cancelamento não enviam nada.

## Se o app fechar

Erros inesperados num quadro não fecham mais o app: vão para `logs/erros.log` com um aviso na tela. Falhas nativas (driver de
vídeo, áudio, codec) ficam em `logs/falhas.log`, e o `iniciar.bat` segura a janela se o app sair com erro.

## Painel de LEDs (Arduino)

Olhos espelhados num painel físico 32×8 (Arduino Uno + 4× MAX7219). Ligações: **VCC→5V, GND→GND, DIN→D11, CS→D10, CLK→D13**.
Grave o firmware com `gravar_arduino.bat` (ou pela IDE do Arduino) e ligue em **Ajustes → Conexões → Painel LED**.
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

- **Eco:** um microfone só, com **cancelamento de eco** (o som que o alto-falante tocou é tirado do microfone). Por isso dá para
  falar por cima da Ayra e conversar com o operador ao mesmo tempo. Com fone de ouvido ou sem cancelamento: **Ajustes → Conversa e IA → Eco do alto-falante**.
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

**Ajustes (F10) → Conversa e IA**: provedor (OpenAI Realtime ou Gemini Live), colar as chaves (Ctrl+V; na tela só aparecem os 4 últimos
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
python test_uso.py         # 51 verificações: grau da voz, Modo de uso, menu agrupado e rolagem, fila de áudio, voz sem travar, modo leve
python test_audio.py       # 37 verificações: eco, microfone único, conversa contínua, ligação e 10 quadros/s
python selftest.py --no-png # 255 verificações do rosto/percepção
```

## Voz robótica com grau e áudio da ligação

- **Grau 0–100 %** (como nos filtros de voz do Animaze): mistura a voz natural com a robótica, alinhadas no tempo (sem eco).
  Ajustes → Conversa e IA → Voz robótica (toque cicla 0/25/50/75/100) ou o controle deslizante da sala do operador. Salvo em
  `NURIA_VOZ_ROBOTICA` (valores antigos `sim`/`1` = 60 %). Comece em 40–60 % se a voz estiver difícil de entender.
- **Sem picotar**: buffer de jitter adaptativo no alto-falante (120 ms, cresce até 400 ms se faltar áudio), blocos de 40 ms,
  processamento de eco/captura e vocoder fora das threads de áudio e do laço de rede, troca de thread a cada 1 ms.
- Nada na tela revela o teleoperador: sem aviso, sem “(voz do operador)” na pergunta.

## Desempenho do corpo 3D e log de tempos

O corpo 3D ajusta a qualidade sozinho (suavização de borda e resolução interna, 4 degraus) conforme o tempo real de desenho:
placa fraca ou Raspberry desce de degrau; com folga estável, sobe. O rosto do visor só é redesenhado quando o 3D redesenha.
`logs/desempenho.log` ganha uma linha a cada 10 s (fps, tempo por quadro, tempo e degrau do 3D, operador conectado). Se estiver
lento, envie esse arquivo: mostra onde está o gargalo no seu computador.

### Visão sem travar a interface (queda de fps após minutos)
Num log real o fps caía de 43 para ~6 em poucos minutos, sem operador, com a CPU saturada. A visão disputava CPU com a
interface: a detecção de rosto rodava na taxa da câmera e as threads de mãos, sorriso e objetos trabalhavam a 8, 6,7 e 2 Hz.
Agora o rosto é detectado no máximo 15×/s (10×/s no modo leve), mãos 5,5 Hz, sorriso 3 Hz e objetos 1,7 Hz, e todas essas
threads rodam com prioridade abaixo do normal no Windows. Se a CPU ficar curta, a visão atrasa um pouco e a interface continua fluida.

# Nuria — Visão e Interpretação do Ambiente com LLM

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
| Pausar câmera | **P**. Ajuda completa: **F1**. |

Estados (texto + forma): **Conectando · Pronta · Ouvindo · Capturando imagem · Pensando · Falando · Erro/Atenção**. A conexão é aberta ao iniciar o app
e reaproveitada entre perguntas; o subtítulo da coluna mostra se está conectada.

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

**Ajustes (F10) → API da OpenAI**: colar a chave (Ctrl+V; na tela só aparecem os 4 últimos caracteres), **Testar conexão**,
trocar **Modelo Realtime** (`gpt-realtime-2.1` / `-mini`) e **Voz**, ou **Remover chave**. Tudo é gravado no `.env`
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
voz.py                      microfone em blocos e reprodução progressiva
credenciais.py              chave fora do código (.env / variável de ambiente)
ia.py, assistente.py        personalidade, estados compartilhados e modo legado (3 chamadas)
painel_assistente.py        coluna: imagem, pergunta, resposta, botões
entrada_chave.py            folha para colar a chave (aba API)
tracker.py, robot.py, …     câmera, rastreamento, olhar, expressões e desenho do rosto
arduino_led.py              painel LED 32×8 (render, mapeamento, serial)
arduino/nuria_leds/         firmware do Arduino Uno
docs/                       ARQUITETURA.md · REGISTRO_TESTES.md · ARDUINO.md
test_*.py, selftest.py      testes automáticos (rodar_testes.bat roda todos)
```

## Testes

`rodar_testes.bat` roda todas as suítes no Windows. Individualmente:

```bash
python test_realtime.py     # 66 verificações do caminho Realtime, com conexão e dispositivos SIMULADOS
python test_nuria.py        # 81 verificações: interface, câmera, falhas e modo legado
python test_api.py         # 23 verificações da aba API (chave pela interface)
python test_arduino.py     # 38 verificações do painel LED (porta serial simulada)
python selftest.py --no-png # 254 verificações do rosto/percepção
```

# Ayra — Controle remoto (etapa 2)

Um operador humano, em outra sala, vê e ouve o ambiente do robô e decide **quem conduz a conversa**: a IA,
a IA com intervenções, ou ele mesmo. Tudo pelo navegador, sem instalar nada no computador do operador.

## Modos

| Modo | Quem fala | O operador pode |
|---|---|---|
| **Autônomo** | IA (Realtime) | Assistir; mandar expressões, olhar, pausar a visão |
| **Assistido** | IA | Tudo acima; frases digitadas saem pela voz da IA; a voz ao vivo dele interrompe a IA |
| **Teleoperado** | Só o operador | A IA fica em silêncio. Voz ao vivo ("Segure para falar") ou frase digitada pela voz local do Windows (gratuita) |

O humano sempre tem prioridade: segurar "Falar" corta a resposta da IA na hora. Se o operador sair, a Ayra volta ao autônomo.

## Voz robótica (opcional)

Vocoder com tom fixo aplicado **à voz da IA e à voz do operador**: as duas saem iguais, e o público não percebe a troca.
Liga em **Ajustes → API → Voz robótica** ou na própria sala do operador. A voz local do Windows usada no
teleoperado também passa pelo filtro, então o teleoperado não gasta a API.

## Como usar (rede local)

1. No robô: **Ajustes (F10) → Controle remoto → Sala do operador** (ligar). Aparecem o **endereço** (ex.: `https://192.168.0.15:8765`)
   e o **PIN** de 6 dígitos. Na primeira vez, o Windows pergunta se libera o Python na rede: permita em **redes privadas**.
2. No computador do operador (mesma rede), abra o endereço no Chrome/Edge. O certificado é do próprio robô: clique em
   **Avançado → Continuar** (o microfone do navegador só funciona em HTTPS).
3. Digite o PIN. O robô mostra o selo vermelho **"Operador conectado · vendo e ouvindo"** enquanto houver operador (pode ser ocultado em **Ajustes → Controle remoto → Aviso na tela**).

Na sala: vídeo (5 quadros/s; **clique na imagem para a Ayra olhar para lá**), áudio do ambiente, "Segure para falar"
(ou barra de espaço), frase digitada, modos, 12 expressões/simulações, **13 gestos do corpo 3D** (tchau, joia, dedo do meio,
apontar, paz e amor, OK, aplaudir, encolher ombros, comemorar, continência, pensar, sim, não), chave **Corpo 3D**,
visão da IA, voz robótica, estado (inclusive o gesto em andamento) e latência. A IA também usa os gestos sozinha.

## No celular

A página se adapta ao celular (iPhone e Android): uma coluna na ordem de uso (vídeo, modo, frase, gestos, expressões),
**“Segure para falar” numa barra fixa embaixo** com o botão de interromper ao lado, áreas seguras do iPhone respeitadas,
campos sem zoom ao digitar e alvos de toque de 44 pt. Deitado, o vídeo fica à esquerda e os controles à direita.
Toque na imagem para direcionar o olhar.

## Arquitetura

```
Robô (Python)                                           Operador (navegador)
 CameraSource ─5 FPS─► CameraTrack ─┐                  ┌─► vídeo + clique = olhar
 RobotMic (48 kHz, mudo enquanto    ├─ WebRTC (DTLS) ──┼─► áudio do ambiente
   o robô fala: sem eco) ─► MicTrack┘   aiortc         │
 assistant.operator_audio ◄─ voz do operador (só com "Falar") ◄─ microfone (eco cancelado no navegador)
 fila de comandos ◄──────── canal de dados "controle" ◄─── modo, expressão, gesto, corpo 3D, olhar, frase, visão, voz
 telemetria (4×/s) ────────► estado, pergunta, resposta, expressão, rostos;  ping → latência
 aiohttp HTTPS :8765 (página + /offer com PIN; 1 operador por vez; 10 PINs errados bloqueiam)
```

## Internet (próximo passo)

A mesma página funciona pela internet sem mudar código usando uma rede privada virtual, por exemplo **Tailscale**
(gratuito): instale no robô e no computador do operador e use o endereço `100.x.y.z` do robô. Sem abrir portas no
roteador e com a criptografia do WebRTC por cima.

## Privacidade e segurança

- Câmera e microfone do robô só são transmitidos com operador conectado; selo vermelho sempre visível no robô.
- PIN de 6 dígitos (gere outro em **Controle remoto → PIN**); um operador por vez; mídia criptografada (WebRTC/DTLS).
- Nada de áudio ou vídeo é gravado. Certificado em `certs/` e configuração em `remote.local.json`, fora do Git.

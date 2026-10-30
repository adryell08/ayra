# Ayra — Registro de testes

> Os testes automáticos abaixo usam **conexão, microfone, alto-falante, webcam e porta serial simulados**. Eles verificam
> a lógica, a ordem dos eventos e o tratamento de falhas, mas não substituem a validação real da seção 2.
> Para repetir tudo no Windows: `rodar_testes.bat`.

## 1. Testes automáticos (21/10/2026)

| Suíte | Escopo | Resultado |
|---|---|---|
| `test_realtime.py` | Caminho Realtime: eventos, imagem antes da resposta, áudio progressivo, interrupção, reconexão | 67/67 |
| `test_nuria.py` | Interface, câmera sob pedido, falhas (câmera, rede, chave, microfone), modo legado | 81/81 |
| `test_api.py` | Aba API: chave pela interface (mascarada), `.env`, teste de conexão | 23/23 |
| `test_arduino.py` | Painel LED 32×8: imagem, mapeamento dos módulos, protocolo, porta serial | 38/38 |
| `test_operador.py` | Etapa 2: sala do operador (WebRTC real em 127.0.0.1), modos, voz robótica | 33/33 |
| `selftest.py` | Rosto, olhar, expressões e percepção herdados do piloto | 254/254 |

### 1.1 Caminho Realtime — `test_realtime.py`

```
Configuração da sessão
  [OK] sessão GA: type realtime, áudio PCM 24 kHz entrada/saída, só áudio
  [OK] modelo e voz configuráveis (padrão gpt-realtime-2.1 / marin)
  [OK] detecção de turno local (turn_detection nulo, controles manuais)
  [OK] transcrição da pergunta em pt para a interface; pode desligar
  [OK] ferramenta expressar com emoções e ações
  [OK] identidade: protótipo chamado apenas Ayra
  [OK] respostas curtas (1–2 frases, ~20 palavras)
  [OK] só fala de si quando perguntarem
  [OK] chave fora da sessão e do repr
Pergunta por voz (simulada)
  [OK] fluxo termina em Pronta
  [OK] estados: (conectando) → ouvindo → (capturando) → pensando → falando
  [OK] session.update é o primeiro evento
  [OK] áudio enviado em blocos de 40 ms a 24 kHz durante a escuta
  [OK] ordem: blocos → commit → imagem → response.create
  [OK] imagem JPEG em data URL
  [OK] pergunta transcrita aparece na interface
  [OK] resposta aparece em texto progressivo
  [OK] áudio da resposta tocado (blocos reproduzidos)
  [OK] tempo até o primeiro áudio medido
  [OK] microfone fechado enquanto fala (meia-duplex, sem eco)
  [OK] voz: imagem apagada da sessão ao fim da resposta (cobrada uma vez)
  [OK] histórico guarda a troca
  [OK] conexão persiste entre perguntas
Pergunta digitada, falhas e cancelamento (simulados)
  [OK] digitada: pergunta e imagem em itens separados, antes do response.create
  [OK] após a resposta, só a imagem sai da sessão (pergunta fica)
  [OK] digitada não usa microfone
  [OK] nada dito: buffer limpo, sem commit nem resposta
  [OK] câmera indisponível: erro claro e nenhuma resposta pedida
  [OK] microfone indisponível: erro sugere digitar
  [OK] Espaço durante a escuta envia na hora
  [OK] envio manual gera commit e resposta
  [OK] Esc cancela a escuta sem pedir resposta
Visão pausada: só conversa (simulado)
  [OK] visão pausada: nenhuma foto tirada nem enviada (texto e voz)
  [OK] modelo é avisado de que não há imagem
  [OK] respostas seguem normalmente
  [OK] retomar a visão volta a enviar a imagem
  [OK] com visão pausada, câmera indisponível não impede conversar
Expressões por função (simulado)
  [OK] chamada de função devolve function_call_output e pede a fala
  [OK] com função: imagem só sai depois da resposta falada
  [OK] rosto chora durante a resposta
  [OK] emoção mantida em toda a fala
  [OK] fala não lê JSON/comando
  [OK] terminada a fala, rosto volta ao automático
Interrupção (barge-in) simulada
  [OK] Espaço durante a fala volta a ouvir em < 300 ms
  [OK] response.cancel enviado
  [OK] interrompida: imagem da pergunta também sai da sessão
  [OK] truncate no trecho ouvido (0 < audio_end_ms < recebido)
  [OK] áudio pendente descartado
  [OK] nova pergunta segue normalmente após a interrupção
Reconexão e encerramento (simulados)
  [OK] queda em repouso: marcada como desconectada
  [OK] próxima pergunta reconecta e responde
  [OK] contexto anterior levado à nova sessão
  [OK] queda durante a fala: erro claro, app segue
  [OK] sem internet: 3 tentativas e erro de conexão
  [OK] sem chave: erro orienta configurar o .env
  [OK] mudança segura: session.update sem trocar modelo/voz nem reconectar
  [OK] troca de voz recria a sessão
  [OK] encerramento fecha a conexão
  [OK] modelo sem 'reasoning': repete a sessão sem o campo e funciona
  [OK] aquecimento conecta ao abrir o app
  [OK] aquecimento + pergunta usam uma única conexão
Transporte WebSocket real contra servidor WebSocket local (sem internet)
  [OK] WebSocket real: Bearer no cabeçalho e modelo na URL
  [OK] WebSocket real: sessão, item e resposta trafegam; áudio tocado
Microfone 24 kHz e reprodução progressiva com PortAudio simulado
  [OK] microfone 24 kHz direto: blocos de 40 ms a 24 kHz e fim da fala detectado
  [OK] microfone 48 kHz convertido: blocos de 40 ms a 24 kHz e fim da fala detectado
  [OK] reprodução progressiva: toca enquanto chega, conta o tocado e mede o volume real
  [OK] clear descarta pendente e informa o trecho ouvido
```

### 1.2 Interface, câmera, falhas e modo legado — `test_nuria.py`

```
Credenciais (RNF01)
  [OK] .env é lido (chave e modelo)
  [OK] repr nunca expõe a chave
  [OK] variável de ambiente prevalece sobre .env
  [OK] sem .env: chave ausente e modelos padrão
  [OK] nenhuma chave 'sk-' no código-fonte
  [OK] .env está no .gitignore
  [OK] sem chave: erro claro ao criar backend
Resposta da IA
  [OK] JSON → texto e emoção
  [OK] texto puro aceito (emoção neutra)
  [OK] ação 'chorar' vira simulação cry
  [OK] ação desconhecida é ignorada
  [OK] prompt informa as simulações disponíveis
  [OK] emoção desconhecida vira neutra
  [OK] imagem reduzida a ≤1024 px antes do envio
  [OK] envelope de fala 0..1 (boca)
  [OK] WAV de 16 bits gerado
  [OK] erros da API mapeados (rede, chave, cota, tempo)
Chamadas reais do SDK OpenAI contra servidor simulado (sem rede)
  [OK] STT: multipart em /audio/transcriptions com idioma pt
  [OK] LLM: modelo ausente → tenta o próximo da lista
  [OK] LLM: imagem JPEG em base64 + pergunta + histórico + JSON
  [OK] LLM: resposta e emoção interpretadas
  [OK] TTS: PCM 24 kHz, voz e entonação da emoção
  [OK] sem rede: AIError de conexão
  [OK] chave inválida: AIError de autenticação
Microfone/alto-falante com PortAudio simulado (RF02)
  [OK] fala detectada e gravação para sozinha no silêncio
  [OK] sala ruidosa: fim da fala ainda é detectado
  [OK] só ruído: devolve None (sem chamar a API)
  [OK] parada manual envia mesmo com voz baixa
  [OK] sem microfone: VoiceError
  [OK] alto-falante toca e boca segue o volume
  [OK] interromper a fala para o áudio em < 100 ms
Foto sob pedido na câmera (RF01)
  [OK] capture_still devolve cópia do quadro atual
  [OK] nenhum quadro fica retido após a foto
  [OK] câmera pausada: sem foto (privacidade)
  [OK] câmera encerrada: sem foto e sem travar
Fluxos (RF01–RF04, RNF02, RNF03)
  [OK] voz: ouvir → transcrever → pensar → falar → pronta
  [OK] pergunta transcrita, resposta e imagem disponíveis
  [OK] ordem de chamadas STT→LLM→TTS e áudio tocado
  [OK] tempos registrados (transcrição, IA, voz)
  [OK] histórico guarda a conversa
  [OK] pergunta digitada é aceita
  [OK] digitada: sem STT, com imagem e resposta
  [OK] sem fala: aviso, sem chamar API
  [OK] câmera indisponível: erro claro, app segue
  [OK] sem internet na IA: erro de conexão
  [OK] sem internet na transcrição: erro de conexão
  [OK] chave inválida: erro e backend recriado na próxima
  [OK] voz online falha → voz do sistema + aviso
  [OK] sem nenhuma voz: resposta fica em texto
  [OK] microfone indisponível: erro sugere digitar
  [OK] tocar de novo durante a escuta envia
  [OK] interrupção (barge-in): fala para e volta a ouvir rápido
  [OK] Esc cancela e volta a pronta
  [OK] exceção inesperada não derruba o app
  [OK] ocupada: não aceita outra pergunta ao mesmo tempo
  [OK] modo simulado responde identificando-se como simulação
  [OK] rosto acompanha estados (pensar/falar)
  [OK] IA pede 'chorar' → rosto executa a simulação de choro
  [OK] emoção fica até o fim da fala, mesmo com outra reação
  [OK] terminada a fala, volta ao automático
Interface (RF05)
  [OK] dark 1280x720: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos
  [OK] light 1280x720: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos
  [OK] contrast 1280x720: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos
  [OK] dark 1920x1080: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos
  [OK] light 1920x1080: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos
  [OK] contrast 1920x1080: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos
  [OK] dark 800x600: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos
  [OK] light 800x600: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos
  [OK] contrast 800x600: coluna cabe na janela, alvos ≥44 pt, rosto e dock não são cobertos
  [OK] dark: contraste texto/coluna ≥ 4,5:1
  [OK] light: contraste texto/coluna ≥ 4,5:1
  [OK] contrast: contraste texto/coluna ≥ 4,5:1
  [OK] contraste do botão azul com texto branco ≥ 3:1
  [OK] Espaço inicia a escuta
  [OK] Enter abre o campo; teclas não acionam atalhos (Q não fecha)
  [OK] Enter envia a pergunta digitada
  [OK] V e o botão da câmera pausam/retomam a visão
  [OK] pressionar o botão dá retorno imediato sem disparar
  [OK] soltar sobre o botão inicia a escuta
Aplicativo completo (--sim, IA simulada, sem câmera)
  [OK] app abre, responde pergunta digitada e fecha sem erro
  [OK] laço não travou durante a resposta (≤ 7 s de parede)
```

### 1.3 Aba API — `test_api.py`

```
Arquivo .env
  [OK] troca a chave preservando comentários e outras opções
  [OK] acrescenta opção nova sem duplicar
  [OK] remover apaga só a chave
  [OK] cria o .env se não existir
  [OK] máscara mostra só prefixo e 4 últimos
  [OK] validação: sk-, tamanho e sem espaços
  [OK] .env continua fora do Git
Folha de entrada da chave
  [OK] digitar + colar (Ctrl+V, sem espaços/quebras)
  [OK] tela nunca mostra a chave inteira
  [OK] teclas de atalho não vazam (Q não fecha o app)
  [OK] Enter salva e fecha
  [OK] chave inválida: mensagem e continua aberta
  [OK] Esc cancela
  [OK] botões com alvo ≥ 44 pt dentro da folha
  [OK] botão Salvar (pressionar e soltar)
Aba API e teste de conexão
  [OK] aba API no menu, com a chave mascarada
  [OK] itens: chave, testar, remover, modelo, voz
  [OK] tocar num item mantém a aba aberta
  [OK] nenhum texto da aba contém a chave
  [OK] testar conexão: OK com servidor (simulado)
  [OK] testar conexão: mostra o motivo da falha
  [OK] sem chave: avisa sem tentar conectar
  [OK] repr da configuração não vaza a chave
```

### 1.4 Painel LED (Arduino) — `test_arduino.py`

```
Rosto 32×8
  [OK] imagem 32×8
  [OK] dois olhos, um em cada metade
  [OK] layout 16×8 | 16×8: cada olho centralizado na sua metade
  [OK] olhos ocupam a altura do painel (≥ 6 de 8 linhas)
  [OK] cada olho tem 5–9 colunas
  [OK] olhar para o lado desloca os olhos no painel
  [OK] olhar extremo: cada olho transita sem sair da sua metade
  [OK] piscar apaga a maior parte dos olhos
  [OK] tamanho dos olhos ajustável
  [OK] interface padrão intacta (render do painel não altera o quadro da tela)
Mapeamento dos módulos e protocolo
  [OK] 32 bytes por quadro
  [OK] entrada à direita: canto esquerdo vai no último módulo, linha 0, bit 7
  [OK] entrada à esquerda: canto esquerdo no módulo 0
  [OK] giro de 90°: canto vai para o fim da linha 0
  [OK] espelho: canto vai para o bit 0
  [OK] de cabeça para baixo: canto vai para o último módulo, linha 7, bit 0
  [OK] pacote: sincronismo, comando, dados e XOR
  [OK] firmware (lógica espelhada): aceita quadro e brilho, ignora lixo e XOR errado
  [OK] padrão de teste: ponto no canto e seta para a direita
Configuração da aba Arduino
  [OK] padrão: desligado, porta automática, brilho baixo
  [OK] preferências salvas e recarregadas (teste nunca reabre ligado)
  [OK] valores inválidos corrigidos
  [OK] aba Arduino no menu de Ajustes
  [OK] aba lista painel, porta, brilho, tamanho, teste e montagem
  [OK] tocar num ajuste mantém a aba aberta e devolve a ação
  [OK] ação chega ao app
Conexão serial (simulada)
  [OK] detecção automática acha o Arduino pelo firmware (ignora outra porta)
  [OK] brilho e quadro chegam ao firmware
  [OK] quadro parado é reenviado ~1×/s (o firmware não apaga)
  [OK] novo quadro substitui o anterior
  [OK] cabo desconectado: status avisa e tenta de novo
  [OK] reconecta sozinho quando a porta volta
  [OK] ao sair, apaga o painel e fecha a porta
  [OK] firmware antigo (v1): conecta e avisa para gravar de novo
  [OK] sem firmware gravado: orienta gravar o nuria_leds
  [OK] nenhuma porta: status claro, app segue
  [OK] porta ocupada (ex.: Monitor Serial aberto): mostra o erro
App com a aba Arduino ligada (sem placa)
  [OK] app roda e fecha normalmente com o painel ligado e sem Arduino
```

### 1.5 Controle remoto e voz robótica (etapa 2) — `test_operador.py`

Servidor e cliente WebRTC reais (aiortc) na própria máquina; câmera, microfone e IA simulados. Além disso, a página
foi aberta num Chromium real (dispositivos falsos): PIN, vídeo 640×480, estado, comandos e "Segure para falar" funcionaram.

```
Voz robótica
  [OK] vozes diferentes saem com o mesmo tom (periodicidade em 105 Hz)
  [OK] volume acompanha a voz original (sem estourar)
  [OK] leve: < 8 ms por bloco de 40 ms
  [OK] silêncio continua silêncio
Sala do operador: WebRTC real em 127.0.0.1 (câmera, microfone e IA simulados)
  [OK] servidor sobe na rede local
  [OK] endereço para o operador
  [OK] página da sala servida pelo robô
  [OK] PIN errado é recusado
  [OK] WebRTC conecta e o canal de comandos abre
  [OK] vídeo do robô chega ao operador (~5 quadros/s, 640×480)
  [OK] áudio do ambiente (microfone do robô) chega ao operador
  [OK] voz do operador só passa enquanto segura “Falar”
  [OK] voz do operador chega convertida para 24 kHz mono
  [OK] comandos chegam ao robô (modo, expressão, olhar, frase, visão, voz)
  [OK] comando desconhecido é ignorado
  [OK] operador conectado/desconectado avisado à interface
  [OK] operador recebe estado da Ayra (~4×/s) e mede latência
  [OK] microfone do robô liberado ao desconectar
  [OK] servidor encerra limpo
  [OK] certificado HTTPS gerado (microfone do navegador exige HTTPS)
  [OK] sala em HTTPS responde
  [OK] certificado e configurações locais fora do Git
Modos de controle no robô (IA simulada)
  [OK] modo inicial autônomo
  [OK] teleoperado: a IA para na hora
  [OK] teleoperado: Espaço/texto no robô não acionam a IA
  [OK] teleoperado: frase do operador sai pela voz local (sem custo de API)
  [OK] frase do operador aparece na coluna
  [OK] voz ao vivo do operador faz a Ayra falar (boca segue o som)
  [OK] soltou “Falar”: volta a Pronta
  [OK] assistido: a IA fala a frase sugerida com a voz dela
  [OK] voz do operador interrompe a resposta da IA (prioridade do humano)
  [OK] operador saiu: a Ayra volta ao modo autônomo
App com a sala do operador ligada
  [OK] app abre a sala e fecha normalmente
```

### 1.6 Corpo 3D e gestos — `test_corpo3d.py`

Modelo gerado pelo script do Blender (`blender/nuria_3d.py`), esqueleto, gestos, desenho OpenGL fora da tela
(EGL, sem placa de vídeo dedicada) e integração com a IA simulada, a sala do operador e o menu. Prévias em
`previews/nuria_3d_gestos.png`.

```
Modelo 3D (gerado pelo script do Blender)
  [OK] pivôs: cabeça, ombros, cotovelos, punhos, 4 dedos + polegar por mão
  [OK] visor próprio para o rosto atual (sem olhos/boca de pontos no modelo)
  [OK] nenhum LED na frente do visor (olhos do Blender removidos)
  [OK] malha leve o bastante para tempo real (333 mil triângulos)
  [OK] índices válidos
  [OK] cada peça presa a um pivô existente
  [OK] script do Blender versionado, sem a matriz de pontos dos olhos
Esqueleto e cinemática inversa
  [OK] pose de repouso: matrizes finitas
  [OK] ossos rígidos: braço e antebraço mantêm o comprimento em todos os gestos
  [OK] punho chega ao alvo em todos os gestos (erro máx. 0.0 cm do modelo)
  [OK] joia: polegar para cima e acima dos dedos fechados
  [OK] dedo do meio: só o médio fica levantado
  [OK] apontar: indicador é o que vai mais longe
  [OK] tchau: mão acima do ombro, dedos para cima
  [OK] sim/não: só a cabeça mexe (braços em repouso)
Animação e transições
  [OK] gesto desconhecido é ignorado
  [OK] gesto conhecido começa
  [OK] movimento contínuo, sem saltos (máx. 0.073 por quadro)
  [OK] ao terminar, volta ao repouso
  [OK] trocar de gesto no meio emenda sem salto
  [OK] cabeça acompanha o olhar (rosto rastreado)
  [OK] movimento reduzido: sem balanço de fala
Desenho 3D (OpenGL)
  [OK] disponibilidade detectada
  [OK] imagem desenhada (robô sobre o fundo)
  [OK] o rosto aparece no visor (e só no visor)
  [OK] fundo igual ao do tema ((np.int64(10), np.int64(12), np.int64(16)))
  [OK] gesto muda a imagem (braços para cima)
  [OK] janela estreita/alta também enquadra
  [OK] prévias salvas (previews/nuria_3d_*.png)
IA e sala do operador
  [OK] ferramenta expressar oferece todos os gestos à IA (inclusive dedo do meio)
  [OK] instruções avisam a IA de que ela tem braços
  [OK] IA (simulada) pede o gesto tchau pela função expressar
  [OK] operador/menu: pedido direto de gesto
  [OK] o mesmo gesto pode repetir
  [OK] gesto inválido é recusado
  [OK] servidor aceita os comandos gesture e body
  [OK] página do operador: um botão por gesto, na mesma ordem
  [OK] página do operador: chave Corpo 3D e estado do gesto
Ajustes → Corpo 3D
  [OK] item Corpo 3D na tela de Ajustes
  [OK] liga/desliga + um item por gesto
  [OK] mostra o motivo quando o 3D não está disponível
  [OK] tocar num gesto mantém a aba aberta
App com o corpo 3D
  [OK] abre com o corpo 3D ligado e fecha normalmente
  [OK] sem OpenGL/moderngl: continua no rosto 2D, sem travar

```

### 1.7 Nome, câmera ao vivo, celular e dedo do meio — `test_maos_e_tela.py`

A página do operador também foi aberta num Chromium com tela de celular (390×844, 360×740 e deitado 844×390): uma
coluna sem rolagem lateral, barra de falar fixa embaixo. O reconhecimento de mão foi conferido com o vídeo de
exemplo do OpenCV Zoo (pontos da mão alinhados; nenhum falso “dedo do meio” em 155 quadros).

```
Nome: Ayra
  [OK] persona: o nome é Ayra
  [OK] instruções da Realtime sem o nome antigo
  [OK] sala do operador: Ayra
  [FALHOU] nenhum texto visível com 'Nuria' no código
  [OK] .env continua o mesmo (variáveis NURIA_* preservadas)
Miniatura ao vivo na coluna
  [OK] câmera só gera miniatura quando a tela pede (pedido registrado)
  [OK] câmera pausada: sem miniatura
  [OK] quadro ao vivo aparece na coluna
  [OK] foto enviada à IA aparece em miniatura no canto (o ao vivo continua)
  [OK] sem câmera ao vivo: volta a mostrar a última foto enviada
  [OK] o envio à IA não mudou (uma foto por pergunta)
Sala do operador no celular
  [OK] viewport cobre a tela toda (iPhone com entalhe) e respeita áreas seguras
  [OK] Falar fica numa barra fixa embaixo (alcance do polegar) com Interromper ao lado
  [OK] uma coluna na ordem de uso (vídeo, modo, frase, gestos…)
  [OK] celular deitado: vídeo à esquerda, controles à direita
  [OK] campos com 16 px (iOS não dá zoom ao digitar)
  [OK] botão de falar sem menu de toque longo nem seleção
  [OK] texto de toque/clique conforme o aparelho
  [OK] alvos de toque ≥ 44 pt (botões e linhas das chaves)
Dedo do meio pela câmera (OpenCV, sem IA)
  [OK] dedo do meio reconhecido
  [OK] também com a mão girada/inclinada
  [OK] mão aberta não é dedo do meio
  [OK] punho fechado não é dedo do meio
  [OK] paz e amor (indicador + médio) não é dedo do meio
  [OK] apontar (indicador) não é dedo do meio
  [OK] uma leitura só não basta (evita falso positivo)
  [OK] duas leituras em 0,8 s confirmam
  [OK] espera 6 s antes de reagir de novo
  [OK] robô reage sozinho: fica bravo e registra o gatilho
  [OK] Ajustes → Reações → Devolver dedo do meio desliga a reação
  [OK] modelo de pontos da mão (OpenCV Zoo) com hash conferido
  [OK] com o corpo 3D ligado, a Ayra devolve o gesto
  [OK] detector real carrega e não acusa gesto numa imagem vazia

```

### 1.8 Gemini Live, economia de tokens e estabilidade — `test_gemini.py`

Gemini simulado (protocolo do Live, sem rede). O fechamento com o operador conectado foi reproduzido: com o corpo 3D ligado,
o app fechava assim que a Ayra (ou o operador) falava — `float()` aplicado ao método `player.level`. Corrigido e coberto.

```
Configuração: provedor, chaves e economia
  [OK] Gemini escolhido pelo .env (NURIA_PROVEDOR=gemini) e chave pelo GEMINI_API_KEY
  [OK] chave do Gemini mascarada e fora do repr
  [OK] validação da chave do Gemini (AIza…)
  [OK] padrões econômicos: teto de 350 tokens por resposta e foto de 512 px
  [OK] .env aceita comentário no fim da linha (como no .env.example)
  [OK] OpenAI: max_output_tokens na sessão (0 = sem limite → "inf")
  [OK] instruções pedem 1 frase de até 15 palavras
  [OK] Gemini: setup com modelo, voz, maxOutputTokens e só áudio
  [OK] Gemini: fim de fala manual (como na Realtime) e transcrições ligadas
  [OK] Gemini: janela deslizante (sessão com imagem não expira em 2 min)
  [OK] Gemini: a mesma ferramenta expressar, com emoções, simulações e gestos descritos
  [OK] contexto das ferramentas: instruções citam rosto, corpo 3D, webcam, gestos vistos e operador
Gemini Live (simulado): pergunta digitada, foto e gesto
  [OK] conecta pelo Gemini (setup → setupComplete)
  [OK] fala em streaming
  [OK] gesto pedido pela IA chega ao corpo 3D
  [OK] termina e volta a Pronta
  [OK] pergunta + foto na mesma vez (texto e imagem JPEG)
  [OK] foto reduzida para economizar (512×384)
  [OK] resposta da ferramenta enviada na hora, em silêncio
  [OK] resposta escrita aparece na coluna
  [OK] rótulo da coluna mostra Gemini Live
Gemini Live (simulado): pergunta falada
  [OK] fala e responde
  [OK] ordem: activityStart → áudio → foto → activityEnd
  [OK] áudio convertido para 16 kHz (1 s de voz ≈ 16 000 amostras)
  [OK] transcrição da pergunta aparece
  [OK] silêncio: nada vai para o Gemini (nenhum token gasto)
Gemini Live (simulado): interrupção e operador
  [OK] interromper para na hora
  [OK] áudio atrasado da resposta cancelada é descartado
  [OK] assistido: o Gemini fala a frase do operador
Aba API: testar conexão com o Gemini
  [OK] teste OK com o provedor escolhido
  [OK] chave recusada: motivo claro
  [OK] sem chave do Gemini: avisa sem conectar
  [OK] erro de conexão nunca mostra a chave
  [OK] aba API: provedor, chave do Gemini e tamanho das respostas
  [OK] nenhum texto da aba mostra a chave
Estabilidade (app não fecha com o operador falando)
  [OK] nível da fala do corpo 3D lê player.level() (antes: TypeError que fechava o app)
  [OK] corpo 3D com a Ayra falando o tempo todo: não fecha
  [OK] erro inesperado num quadro: registra em logs/erros.log e segue
  [OK] iniciar.bat segura a janela e aponta os logs se fechar com erro
  [OK] vídeo do operador lê o último quadro (não disputa a foto da pergunta)
  [OK] falhas nativas (driver/áudio/vídeo) vão para logs/falhas.log

```

### 1.9 Áudio duplex, conversa contínua e ligação — `test_audio.py`

Sinais sintéticos (fala, eco com atraso e reverberação). Validar com microfone e alto-falante reais (seção 2.7).

```
Cancelamento de eco (sala simulada)
  [OK] atraso de 40 ms: eco do robô cai 33 dB (a IA/operador não ouvem o robô)
  [OK] atraso de 40 ms: falando junto, a voz da pessoa passa inteira (correlação 1.00, perda 0.1 dB)
  [OK] atraso de 250 ms: eco do robô cai 32 dB (a IA/operador não ouvem o robô)
  [OK] atraso de 250 ms: falando junto, a voz da pessoa passa inteira (correlação 1.00, perda 0.1 dB)
  [OK] o eco da sala mudou: reaprende em segundos (23 dB depois)
  [OK] leve: 0.14 ms por bloco de 16 ms
Reamostragem contínua (sem estalos entre blocos)
  [OK] 48 kHz → 16 kHz em blocos de tamanhos variados: 16001 amostras (≈16000)
  [OK] sem descontinuidade nas emendas
  [OK] 16 kHz → 48 kHz para o operador (Opus)
Microfone único repartido (IA + operador)
  [OK] microfone fechado enquanto ninguém escuta (privacidade)
  [OK] abre um microfone só para os dois assinantes
  [OK] IA recebe 24 kHz e operador 48 kHz (8.0 s e 8.0 s de áudio)
  [OK] eco do alto-falante removido no caminho real (captura 48 kHz + referência 24 kHz): 35 dB
  [OK] ninguém escutando: o microfone fecha sozinho
  [OK] meia-via: microfone mudo enquanto o robô fala
  [OK] fone de ouvido: passa direto (sem eco para cancelar)
  [OK] operador recebe blocos de 20 ms a 48 kHz
Conversa contínua: escuta à espera da fala
  [OK] espera a pessoa falar (sem prazo) e devolve a fala
  [OK] envia só a fala + 0,4 s antes (sem cortar a 1ª sílaba): 1.71 s
Conversa contínua na IA (OpenAI simulada) e barge-in por voz
  [OK] travar o microfone: conversa contínua ligada
  [OK] fala → resposta sem apertar nada
  [OK] barge-in: falar por cima interrompe a IA na hora
  [OK] e a nova pergunta segue (commit + resposta)
  [OK] tocar no botão (ou Espaço) com o microfone travado encerra a conversa contínua
  [OK] teleoperado: a IA não escuta (o microfone fica para a ligação com o operador)
Voz do operador: detecção de fala (microfone travado não trava a Ayra)
  [OK] silêncio do operador (travado) não ocupa o alto-falante nem muda o estado
  [OK] voz do operador interrompe a IA e sai pelo robô (com pré-roll)
  [OK] pausa do operador: devolve a palavra (Pronta)
Interface: arrastar para travar, ajustes e sala do operador
  [OK] arrastar o botão de falar para cima trava o microfone (sem disparar um clique)
  [OK] clique simples continua sendo 'falar uma vez'
  [OK] tecla L trava/destrava
  [OK] Ajustes → Áudio e conversa: conversa contínua e modo do eco
  [OK] Ajustes → Controle remoto → Vídeo: 12 quadros/s (5, 12, 15)
  [OK] servidor: 12 quadros/s por padrão; 5 e 15 disponíveis
  [OK] sala do operador: deslizar para cima trava o 'Falar' (como no WhatsApp); T no teclado
  [OK] sala do operador mostra FPS e o modo de áudio
  [OK] microfone do robô para o operador sai do mesmo microfone, sem eco (HubMic)

```

## 2. Validação real no computador da apresentação (preencher)

Primeiro: `verificar_nuria.bat` (conecta de verdade na Realtime e mede o 1º áudio). Cada pergunta fica em `logs/sessoes.jsonl`
com `primeiro_audio` e `resposta_completa` (segundos, a partir do fim da fala).

### 2.1 Demonstração — cenas diferentes

| # | Cena | Pergunta | Resposta obtida | 1º áudio (s) | Resultado |
|---|---|---|---|---|---|
| 1 | Mesa com notebook e garrafa | “O que está sobre a mesa?” | | | ☐ OK |
| 2 | Pessoa segurando um objeto | “O que eu estou segurando?” | | | ☐ OK |
| 3 | Folha com texto à mão | “O que está escrito no papel?” | | | ☐ OK |
| 4 | Continuação | “E de que cor ele é?” | | | ☐ OK |
| 5 | Expressão | “Você consegue chorar?” (rosto chora a fala toda) | | | ☐ OK |
| 6 | Identidade | “O que você é?” (protótipo de estudantes chamado Ayra) | | | ☐ OK |

### 2.2 Tempo real e falhas

| # | Como provocar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| R1 | Pergunta longa e Espaço no meio da resposta | Para na hora e volta a ouvir; a próxima resposta não repete o trecho cortado | | ☐ OK |
| R2 | Resposta falada com caixa de som alta | Ela não “ouve” a si mesma (microfone fechado enquanto fala) | | ☐ OK |
| F1 | Desligar o Wi-Fi e perguntar | “Sem conexão…” após 3 tentativas; religar e perguntar de novo funciona | | ☐ OK |
| F2 | Desligar o Wi-Fi durante uma resposta | “A conexão com a OpenAI caiu…”; próxima pergunta reconecta | | ☐ OK |
| F3 | Pausar a câmera (**P**) e perguntar | Erro de câmera; nada é enviado | | ☐ OK |
| F4 | Chave errada no `.env` | “Chave da OpenAI inválida…”; corrigir e perguntar sem reiniciar | | ☐ OK |
| F5 | Silêncio após Espaço | Aviso “Não ouvi nada…” após ~7 s | | ☐ OK |
| F6 | Microfone desativado no Windows | Erro sugerindo digitar; pergunta digitada funciona | | ☐ OK |
| F7 | Redimensionar a janela durante “Pensando…” | Rosto e coluna continuam animando | | ☐ OK |

### 2.3 Controle remoto (etapa 2, rede local)

| # | Como testar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| C1 | Ligar a sala e abrir o endereço em outro computador | Página abre (aceitar o certificado), PIN conecta, selo vermelho no robô | | ☐ OK |
| C2 | Operador clica na imagem | A Ayra olha para o ponto clicado | | ☐ OK |
| C3 | Teleoperado + "Segure para falar" | A voz do operador sai pelo robô, boca acompanha; a IA não responde | | ☐ OK |
| C4 | Assistido + frase digitada | A IA fala a frase com a voz dela | | ☐ OK |
| C5 | Voz robótica ligada | IA e operador soam iguais | | ☐ OK |
| C6 | Fechar a página do operador | Selo some; a Ayra volta ao modo autônomo | | ☐ OK |
| C7 | PIN errado | Recusado; após 10 erros exige novo PIN | | ☐ OK |

### 2.4 Corpo 3D e gestos

| # | Como testar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| G1 | **F3** (ou Ajustes → Corpo e gestos) | Corpo 3D com os olhos atuais no visor; fluido (≥ 25 quadros/s) | | ☐ OK |
| G2 | Andar na frente da câmera | A cabeça gira acompanhando o rosto | | ☐ OK |
| G3 | Perguntar “dá tchau pra turma” | A IA fala e acena com a mão | | ☐ OK |
| G4 | Sala do operador → botões de gesto (joia, dedo do meio…) | Gesto aparece no robô; estado “Gesto” na sala | | ☐ OK |
| G5 | Operador desliga “Corpo 3D” | Robô volta ao rosto 2D; gestos viram aviso | | ☐ OK |
| G6 | Painel do Arduino ligado com o 3D | Painel continua mostrando os olhos | | ☐ OK |

### 2.5 Câmera ao vivo, celular e dedo do meio

| # | Como testar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| M1 | Abrir o app com a webcam | Coluna mostra a câmera ao vivo; ao perguntar, a foto enviada aparece no canto (“Enviada”) | | ☐ OK |
| M2 | Sala do operador num celular (mesma rede) | Uma coluna, barra “Segure para falar” embaixo; tocar na imagem move o olhar | | ☐ OK |
| M3 | Celular deitado | Vídeo à esquerda, controles à direita | | ☐ OK |
| M4 | Mostrar o dedo do meio para a câmera (IA desligada ou `--ia simulada`) | Ayra fica brava; com o corpo 3D ligado, devolve o gesto | | ☐ OK |
| M5 | Mão aberta e punho fechado | Não disparam reação | | ☐ OK |
| M6 | Joinha, paz e amor, OK e aceno (corpo 3D ligado) | Ayra devolve joia, paz, OK e tchau | | ☐ OK |

### 2.6 Gemini Live e estabilidade

| # | Como testar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| GL1 | Ajustes → Conversa e IA → colar a chave do Gemini → Provedor: Gemini Live → Testar conexão | “OK · conectou em … · gemini-3.8-live” | | ☐ OK |
| GL2 | Perguntar por voz e por texto com a câmera | Resposta curta (1 frase), com foto; coluna mostra “Gemini Live” | | ☐ OK |
| GL3 | Pedir “dá tchau” / “chora” | Gesto/expressão aparecem | | ☐ OK |
| GL4 | Interromper (Espaço) no meio da fala | Para na hora e volta a ouvir | | ☐ OK |
| E1 | Operador conectado, corpo 3D ligado, “Segure para falar” por 5 min | App não fecha | | ☐ OK |

### 2.7 Conversa contínua e ligação

| # | Como testar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| A1 | Arrastar “Clique para falar” para cima e conversar | Responde sem apertar nada; fala por cima interrompe | | ☐ OK |
| A2 | Alto-falante alto, sem falar | Ela não se interrompe sozinha (eco cancelado) | | ☐ OK |
| A3 | Teleoperado + operador com microfone travado (T) | Os dois falam ao mesmo tempo; operador não ouve o próprio eco | | ☐ OK |
| A4 | Se houver eco: Ajustes → Conversa e IA → Eco do alto-falante → Meia-via | Volta ao comportamento antigo, sem eco | | ☐ OK |
| A5 | Vídeo do operador a 10 quadros/s | Fluido; se a rede travar, use 5 | | ☐ OK |

### 2.8 Modo de uso, menu, áudio sem atraso e modo leve

| # | Como testar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| U1 | Ajustes → Modo de uso | Some tudo, menos rosto, botão de falar, conversa e o ponto de câmera | | ☐ OK |
| U2 | No Modo de uso: F10; ou segurar 1,2 s o canto superior direito | Volta aos Ajustes; o anel mostra o progresso | | ☐ OK |
| U3 | No Modo de uso: Esc, D, F1 | Nada fecha nem abre | | ☐ OK |
| U4 | Ajustes → Conexões (lista longa): roda, arrastar, barra, PgDn | Rola suave, sem pular de volta ao início | | ☐ OK |
| U5 | Operador fala pelo celular com o robô conectado | Voz contínua e compreensível (sem “travar”) | | ☐ OK |
| U6 | Bater palmas perto do robô com o operador ouvindo | Atraso bem menor que antes (~0,8 s); medir com um cronômetro | | ☐ OK |
| U7 | `iniciar.bat --leve` no Raspberry Pi 4 | Fluido a 30 quadros/s com o operador conectado | | ☐ OK |

Automático (`python test_uso.py`, 42/42): fila de áudio limitada a 60 ms mesmo com deriva de relógio; reamostragem contínua sem
degraus; buffer de jitter e reagrupamento; voz ao vivo mais inteligível; menu agrupado e rolagem (roda, arrastar, barra, PgDn);
Modo de uso no laço do aplicativo (entrada, saída por F10 e toque longo, teclas bloqueadas); flags do modo leve.

### 2.9 Voz com grau e ligação

| # | Como testar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| U8 | Sala do operador: arrastar “Voz robótica” de 0 a 100 % falando | Muda na hora; 0 % é a voz natural; 40–60 % é compreensível | | ☐ OK |
| U9 | Ligação longa (5 min) com corpo 3D ligado, operador falando | Áudio contínuo, sem picotar | | ☐ OK |
| U10 | Modo teleoperado: olhar a tela do robô | Nenhuma mensagem de “teleoperado” nem “(voz do operador)” | | ☐ OK |
| U11 | Rodar 5 min com corpo 3D, sem operador; ler `logs/desempenho.log` | fps estável (sem cair para ~6); `cpu=` bem abaixo de 300 % | | ☐ OK |

### 2.10 Conversas salvas, repetir e encerrar por voz

| # | Como testar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| U12 | Fazer 2 perguntas, abrir Ajustes → Conversas | A conversa aparece na lista (“2 trocas”) e em balões à direita; a busca filtra | | ☐ OK |
| U13 | Fechar e abrir o app, retomar a conversa e perguntar “do que falávamos?” | A IA lembra o assunto | | ☐ OK |
| U14 | Tocar na seta de repetir após uma resposta | Repete a mesma fala, sem demora de IA | | ☐ OK |
| U15 | Com o microfone travado, dizer “Nuria, encerrar conversa” | Responde nada, solta o microfone | | ☐ OK |

### 2.11 Memória

| # | Como testar | Esperado | Obtido | Resultado |
|---|---|---|---|---|
| M1 | Dizer “lembre-se de que a sala do evento é a 12”, mudar de assunto, abrir Conversas → Nova conversa e perguntar “qual é a sala?” | A IA responde 12 | | ☐ OK |
| M2 | Fechar e abrir o app e repetir a pergunta | Ainda responde 12 | | ☐ OK |
| M3 | Dizer “esqueça isso” e perguntar de novo | Não sabe mais | | ☐ OK |
| M4 | Operador registra instrução na sala, devolve ao modo autônomo e pergunta algo relacionado | A IA segue a instrução sem contradizer | | ☐ OK |
| M5 | Ajustes → Memória desligada, repetir M1 | Não lembra; `logs/memoria.db` não cresce | | ☐ OK |
| M6 | Comparar o tempo até a primeira fala com a memória ligada e desligada (log de desempenho) | Diferença imperceptível (< 50 ms) | | ☐ OK |
| M7 | Ligar a sala, abrir `https://localhost:8765/memoria` no PC (aceitar o certificado) e entrar com o PIN, sem operador | Lista instruções e preferências; Revogar some da lista e a IA deixa de seguir | | ☐ OK |
| M8 | Abrir o painel no celular com o robô rodando por 5 min (3D ligado) e comparar `desempenho.log` com/sem a página aberta | fps igual | | ☐ OK |
| M9 | Conversas → Tab → aba Memória → Revogar | Some na hora; aviso "Memória atualizada" | | ☐ OK |

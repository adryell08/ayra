# Nuria — Registro de testes

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
  [OK] identidade: protótipo chamado apenas Nuria
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
  [OK] operador recebe estado da Nuria (~4×/s) e mede latência
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
  [OK] voz ao vivo do operador faz a Nuria falar (boca segue o som)
  [OK] soltou “Falar”: volta a Pronta
  [OK] assistido: a IA fala a frase sugerida com a voz dela
  [OK] voz do operador interrompe a resposta da IA (prioridade do humano)
  [OK] operador saiu: a Nuria volta ao modo autônomo
App com a sala do operador ligada
  [OK] app abre a sala e fecha normalmente
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
| 6 | Identidade | “O que você é?” (protótipo de estudantes chamado Nuria) | | | ☐ OK |

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
| C2 | Operador clica na imagem | A Nuria olha para o ponto clicado | | ☐ OK |
| C3 | Teleoperado + "Segure para falar" | A voz do operador sai pelo robô, boca acompanha; a IA não responde | | ☐ OK |
| C4 | Assistido + frase digitada | A IA fala a frase com a voz dela | | ☐ OK |
| C5 | Voz robótica ligada | IA e operador soam iguais | | ☐ OK |
| C6 | Fechar a página do operador | Selo some; a Nuria volta ao modo autônomo | | ☐ OK |
| C7 | PIN errado | Recusado; após 10 erros exige novo PIN | | ☐ OK |

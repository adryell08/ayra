# Sala do operador: sem API no teleoperado, rede que reconecta, painel novo e voz artificial única

## 1. Operador ↔ usuário sem API paga
- **Teleoperado = 0 mensagens ao modelo** (medido com servidor Realtime falso, `medir_custo_operador.py` → `avaliacao/custo_operador.json`; guardado por `test_operador_sem_api.py`).
  Antes: frase do operador (autônomo/assistido) = 1 `response.create`; instrução e nota de memória = 1 item cada, **inclusive em teleoperado**. Depois: em teleoperado
  frase, instrução, nota, voz ao vivo, pergunta digitada e "falar" no robô = 0.
- A voz ao vivo do operador e a frase digitada no teleoperado já iam direto (áudio ao player / voz local SAPI). O que vazava eram a **instrução** e a **nota de memória**:
  agora ficam guardadas (até 8) e chegam ao modelo na volta ao autônomo/assistido (nada se perde; se a memória já entra no contexto da volta, não duplica).
- Mudança visível ao usuário final: **nenhuma**. Opcional, **não ativado**: em autônomo/assistido a frase do operador ainda passa pela IA (1 resposta); poderia sair pela voz local
  (0 API), mas mudaria a voz percebida: decisão do Adryell.

## 2. Queda da rede local — diagnóstico e correção
Causas (no código): (a) o painel **não reconectava**: `connectionstatechange` só mostrava "recarregue a página"; (b) sem vigia de silêncio (o robô manda estado 4×/s; ninguém checava);
(c) o servidor derrubava o operador no **primeiro** `disconnected` do ICE (que costuma se recuperar sozinho com Wi-Fi/troca de rede) e (d) **devolvia o robô ao autônomo na hora**.
Correção: `operador_web/reconect.js` (backoff 1/2/4/8/15 s ±20 %, vigia de 5 s sem mensagem, fila de comandos críticos, "Tentar agora", volta da rede/Wi-Fi dispara nova tentativa);
servidor: tolerância `ice_grace` 6 s para `disconnected` e `drop_grace` 20 s antes de anunciar "sem operador" (o robô só volta ao autônomo depois disso; se o operador volta antes, nada muda),
status "operador reconectando". Indicador neutro no painel: Online / Reconectando… (tentativa n) / Sem conexão.
Comandos durante a queda: modo/visão/corpo/voz = só o último vale; frases/instruções na ordem (máx. 5, ≤ 60 s); interromper só se ≤ 5 s; "falar" (PTT), olhar, expressão e gesto **não** são guardados.
PIN errado/bloqueado não fica tentando. Testes: `test_reconect.js` (relógio simulado), `test_reconexao.py` (WebRTC real em 127.0.0.1, queda e volta), `test_painel.py` (Chrome real: queda e volta sozinho).
**Não validado em rede real:** firewall do Windows (UDP dinâmico do WebRTC + porta HTTP 8765), mDNS/troca de IP do robô (o endereço mostrado no robô é o IP; se o DHCP mudar, o link muda),
sono do notebook/celular, roteador com isolamento de clientes, Wi-Fi 2,4 GHz saturado. Reserve o IP no roteador e libere a porta no firewall.

## 3. Painel redesenhado (`operador_web/index.html`)
Uma tela só (sem rolagem, `100dvh`): esquerda Gestos (3D) + chave; centro vídeo AO VIVO, faixa de ícones (Autônomo⇄Teleoperador, Assistido, Visão on/off, microfone segure/trave, Parar),
conversa "Ayra diz: Digite uma frase..." + enviar + alerta no canto; direita medidores de onda Teleoperador (ícone de mic) e Usuário, ícone de robô + barra 0–100 %, Expressões / Olhos (2D) + chave;
rodapé ESTADO. Celular/tablet: gestos e voz/expressões abrem em painel por cima. Estado nunca só por cor (texto On/Off, ●/○), foco visível, aria, alvos ≥ 44 px, contraste AA conferido nos dois temas.
Leve: sem bibliotecas nem rede externa; ondas a ≈ 15 quadros/s e paradas com a aba escondida.
Decisões que o desenho deixou em aberto: a chave "Olhos 2D" é o inverso da chave "Gestos (3D)" (mesmo comando `body`); "Assistido" ficou como botão extra; Pergunta/Resposta da conversa continuam na faixa ESTADO (como já era, truncadas; esconder é uma escolha de privacidade do Adryell).

## 4. Voz artificial única
Já havia **uma só máscara** (`VoiceMask`): voz da IA (`assistente_realtime.py` linha do `process(..., "ai")`), frase do operador (SAPI → máscara) e voz ao vivo do operador usam a mesma instância e o mesmo grau.
Falha da máscara ⇒ bloco descartado (nunca sai o original). A barra 0–100 % substitui os três perfis: 0 = natural, 100 = só filtro, meio = mistura (o original continua audível; aviso mantido). Custo ≈ 1,6 ms por bloco de 40 ms (≈ 4 % de um núcleo), `warm()` carrega o filtro uma vez.
"Voz do OpenBot": o único entregável anterior é o experimento Supertonic 3 (`docs/EXPERIMENTO_TTS_SUPERTONIC.md`): funciona em português, mas pico ~450 MB e 1–2 s por frase → descartado para fala ao vivo; só candidato opcional para frases digitadas, sujeito a o Adryell ouvir as amostras e aceitar a licença OpenRAIL-M. Não encontrei no repositório nenhum pedido formal além disso, nem como identificar qual motor o OpenBot usa.
Opção mais viável sem API paga: manter a máscara única (já pronta); para frase digitada, SAPI + máscara (leve). Neural local só se o Adryell aprovar o custo de memória.

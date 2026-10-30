# Memória, instruções vigentes e continuidade

Implementação do MVP da especificação de memória (29/10/2026), adaptada ao Nuria real: **um robô e um usuário por vez**, os três
modos (autônomo, assistido, teleoperado) e a conversa por OpenAI Realtime ou Gemini Live. O caminho de voz não mudou.

## Mapa do código real
| Parte | Onde | Papel na memória |
|---|---|---|
| Modos | `assistente_realtime.py: set_mode` | registra `mode_change`; ao sair de teleoperado marca o resumo |
| Fala da pessoa | transcrição (`input_audio_transcription.completed`) e `_text_flow` | evento `utterance`/`text` |
| Resposta da IA | `response.done` | evento `response` |
| Operador | `operator_say`, `on_operator_audio`, comando `instruction` | `say`, `voice` (só duração), `instruction` |
| Sessão | `Assistant.session_id` (mesmo id do histórico) | `sessions.id` |
| Contexto | `_restore_context`, `_respond` | itens de texto na sessão Realtime |
| Armazenamento | `memoria.py` → `logs/memoria.db` | SQLite WAL, FTS5, thread própria |

O áudio do operador **não é transcrito** (vai direto ao alto-falante): só se grava quando falou e por quanto tempo. O que ele digita
em "A Ayra diz" e as instruções que registra ficam com texto.

## O que a IA recebe (e quando)
- **Sessão nova** (abrir o app, nova conversa, retomar, queda de conexão): instruções vigentes + preferências do usuário (marcadas como
  "não verificado") + conhecimento validado. Máx. 1100 caracteres. Nada é enviado se não há nada.
- **Volta do operador** (saiu de teleoperado, ele falou ou digitou): um resumo curto antes da próxima resposta — o que o operador
  disse/escreveu, duração da fala sem transcrição, últimas trocas, instruções vigentes.
- **Instrução do operador** (botão na sala dele): entra na sessão na hora e fica gravada.
- Nunca o histórico inteiro e nenhuma chamada a outro LLM.

## Como aprende (regras, sem LLM)
Frases explícitas da pessoa ou do operador, frase a frase, ignorando perguntas: "lembre-se de que…", "a partir de agora…",
"sempre/nunca…", "não esqueça de…" → **instrução**; "meu nome é…", "me chame de…", "eu gosto de…" → **preferência candidata**;
"esqueça isso/tudo", "pare de…" → **revoga**. Instrução parecida (≥60 % das palavras) substitui a antiga; em conflito vale a primeira da
lista (operador antes, depois a mais recente). Preferências só viram conhecimento global por `validate_memory(id, revisor)`.
Limitação: instruções contraditórias com palavras diferentes coexistem até alguém dizer "esqueça isso".

## Gestão (com ou sem operador)
| Onde | Quem | O que faz | Custo no robô |
|---|---|---|---|
| **Conversas → aba Memória** (ícone de balão, Tab alterna) | quem está no robô | ver instruções (com autor) e preferências; Revogar / Apagar | lê o banco 1× ao abrir a aba (≈1 ms); desenho ≈4 ms só enquanto aberta |
| **`/memoria` no navegador** (mesmo PIN da sala; não precisa de operador conectado) | operador ou o próprio usuário, no celular ou no PC (`https://localhost:8765/memoria`) | nova instrução, editar, revogar, validar/rejeitar preferência, apagar, buscar no que foi dito, métricas, esquecer tudo | consulta numa thread de prioridade baixa, fora do laço do WebRTC e do desenho; responde "nada mudou" em poucos bytes; máx. 20 pedidos/s |
| **Voz** | o usuário | "lembre-se de que…", "esqueça a instrução sobre X", "esqueça isso/tudo"; "o que você lembra de mim?" (a IA responde com o que recebeu da memória) | nenhum além do normal |

Toda mudança feita no painel ou na aba vira um aviso curto na sessão da IA na hora ("a instrução X foi revogada; não a siga mais").
O painel atualiza sozinho a cada 8 s só se a versão da memória mudou; com a página fechada não há custo nenhum.

## Privacidade e controle
- Ajustes → Conversa e IA → **Memória** liga/desliga (salvo em `audio.local.json`); `--sem-memoria` ou `NURIA_MEMORIA=off` desliga; `--sem-registro` bloqueia.
- **Esquecer tudo** (toque duas vezes): apaga eventos, instruções e preferências. Apagar uma conversa em **Conversas** apaga também o que foi aprendido nela.
- Retenção de eventos: 90 dias (`NURIA_MEMORIA_DIAS`); instruções vigentes ficam até serem revogadas.
- Cada registro tem `user_id`; leitura e busca sempre filtram por usuário. O banco fica em `logs/` (fora do Git, como `*.db`).

## Falhas, métricas e rollback
- Escrita em thread própria, fila de 2000: se encher, descarta e conta (`dropped`). Após 5 erros seguidos a memória se desliga sozinha e a voz segue normal.
- `Memory.stats()`: gravados, descartados, fila, erros, p50/p95 de escrita, tempo do último contexto. No teste simulado: enfileirar 500 eventos ≈ 2 ms, montar contexto ≈ 1 ms.
- **Rollback**: desligar a memória (menu ou `--sem-memoria`) ou apagar `logs/memoria.db`. Nenhum outro arquivo depende dele.

## Fora do MVP (fase 2+)
Embeddings, consolidação episódica→semântica, MCP, PostgreSQL, vários usuários com autenticação (o esquema já tem `user_id`/`robot_id`),
transcrição do áudio do operador (exigiria STT local ou na nuvem em paralelo) e exportação Markdown.

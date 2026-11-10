# RAG — Etapa 4: ferramenta `lembrar` na voz (implementada, DESLIGADA no piloto) + piloto do Adryell

## Piloto em vigor (decisão do Adryell)
- **Só para o Adryell, só no texto digitado.** `rag.local.json` (na pasta do app, ignorado pelo git) contém **somente** `{"rag_ligado": true}`.
  Vale na **próxima abertura** do app (o app que está aberto não foi tocado e roda o código antigo).
- **A voz continua exatamente como antes** no piloto: a ferramenta `lembrar` só entra na sessão de voz com **`"rag_voz": true`** (chave separada,
  desligada por padrão; **não** está no arquivo do piloto). Assim "ligar o RAG" não muda a fala por conta própria.
- **Como usar:** abra o app, digite perguntas normalmente. Se a memória tiver algo relacionado, a resposta usa (um item de dados some da conversa depois de cada resposta).
  Perguntas sem resposta na memória seguem como antes.
- **Como desligar (na hora, sem reiniciar):** abra `/rag` no navegador (mesmo PIN do painel de memória; Ajustes → Conexões) e toque em **Desligar agora**.
  Para desligar de vez: apague `rag.local.json` ou ponha `"rag_ligado": false` (vale na próxima pergunta; o app relê o arquivo quando muda). O painel só consegue
  **religar** se o arquivo autoriza.
- **Backup do banco real (antes da primeira atualização):** `logs/backups/memoria.db.pre-rag-2026-11-01.bak` (cópia pela API de backup do SQLite, em modo somente
  leitura, integridade `ok`, mesmas contagens). O banco real hoje **não tem nenhum registro de memória** (0 eventos, 0 instruções, 0 memórias): nada a perder nem a apagar
  pela retenção de 20 dias; a contagem só passa a valer para o que for revogado daqui em diante.

## `lembrar` na voz (só com `rag_ligado` + `rag_voz`)
| Regra aprovada | Implementação |
|---|---|
| Registrada só com RAG ligado | `RealtimeAssistant._tools()`: `expressar` sempre; `lembrar` somente com `rag_ligado` **e** `rag_voz`. Liga/desliga muda a sessão (OpenAI: `session.update`; Gemini: reconecta, pois o conjunto de ferramentas entra na chave da sessão). |
| Teto de 2 continuações por pergunta | `max_continuacoes_lembrar` (padrão 2). Cada resposta à ferramenta que pede a fala conta 1. Ao estourar: `function_call_output` com `limite_de_consultas`, **sem novo `response.create`**, estado "Pronta" com aviso neutro ("Não consegui responder agora. Pode repetir?"). |
| Busca fora de captura/reprodução, com prazo e plano B | A chamada roda numa thread própria (a thread de leitura da Realtime nunca espera); `prazo_busca_ms` → `prazo_excedido` (honesto: "nada_encontrado", sem inventar) e o resultado tardio é descartado. |
| Cancelamento por barge-in / modo / sessão | `cancel()`, troca para teleoperado e nova sessão invalidam a busca: nenhuma resposta à função, nenhum `response.create`, nada tardio falado. RAG desligado no painel: resposta `rag_desligado` e a ferramenta sai da sessão na pergunta seguinte. |
| Gemini com caminho próprio | O `GeminiAdapter` responde `lembrar` **dentro da vez**, com o resultado real (sem `SILENT`), via `tool_handler` do assistente; o modelo fala na mesma vez (sem `response.create`). Teto: a 3ª consulta da mesma pergunta recebe `limite_de_consultas`. Roda na thread leitora **do adaptador** (não na interface/áudio). |
| Identidade | Sempre a da sessão (`memory.user_id`); `usuario`/`user_id` que o modelo tente passar nos argumentos são ignorados (testado). |
| Item da pergunta | A própria pergunta do turno (já gravada como fala) é excluída do resultado. |

## Medidas e o que continua PENDENTE
- Simulado (`medir_lembrar_voz.py`, servidor Realtime falso **sem latência de rede**): `lembrar` custa **1 pedido a mais** por pergunta (2 `response.create`) e a busca ≈ 1,9 ms; o
  tempo local não mudou de forma mensurável (p50 51 ms nos dois). **Não é latência real.**
- **PENDENTE (exige sessão real):** tokens de entrada/saída com × sem `lembrar` (`logs/uso_api.jsonl`), tempo até o primeiro áudio com a ida e volta extra, frequência de chamadas
  desnecessárias do modelo, qualidade da fala depois do resultado, e o Gemini real (o contrato de `toolResponse` com resultado foi validado só no servidor simulado).
- Antes de ligar `rag_voz` no piloto: medir uma sessão real curta (10–20 perguntas) e comparar; só então decidir.

## Testes
`test_lembrar_voz.py` (25): registro por configuração (inclui o piloto sem voz), fluxo `lembrar → resultado → fala`, teto de 2, prazo estourado, thread de leitura livre, barge-in, teleoperado,
desligar o RAG, Gemini (resultado real, sem continuação, teto), voz do piloto inalterada. Regressões obrigatórias: `test_ferramentas`, `test_fila_operador`, `test_orquestrador`.

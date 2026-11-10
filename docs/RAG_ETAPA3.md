# RAG — Etapa 3: pergunta digitada (+ preparo da voz, Etapa 4)

Autorizada pelo Adryell. **`rag_ligado` continua DESLIGADO por padrão**; só liga com `rag.local.json` (`{"rag_ligado": true}`, arquivo ignorado pelo git).
Sem API paga, sem rede. Testes: `test_rag_texto.py` (37), `test_contexto.py` (68), `test_aceite_pm.py` (8), `test_avaliacao_rag.py` (33).

## O que mudou
| Decisão | Implementação |
|---|---|
| **RAG só no texto digitado** | `RealtimeAssistant._rag_texto` (no fluxo de pergunta digitada, thread de trabalho do texto): monta o contexto com prazo e plano B e manda **um item de DADOS** antes do pedido de resposta; depois da resposta o item **sai da sessão** (`conversation.item.delete`, como a foto). A fala (voz) **não faz recuperação** e a ferramenta `lembrar` **não está registrada**. |
| **Contexto da sessão com RAG ligado** | `_send_memory_context` usa o `ContextAssembler` (orçamento por categoria, conflito marcado). Desligado: texto idêntico ao de hoje (paridade testada, inclusive a sequência de eventos enviada ao servidor). |
| **Conflito (C05)** | Instruções vigentes que começam igual (≥ 3 palavras) e depois divergem geram, no contexto, "Possível conflito entre instruções vigentes (nenhuma foi apagada…)", com as duas e a mais recente indicada. Nada é apagado nem escolhido; o gerador menciona ou pergunta. É heurística: pode marcar conflito onde não há (ex.: "Sempre responda em uma frase" × "Sempre responda em português"). |
| **Painel privado do RAG** | `/rag` (mesmo PIN/token do painel de memória) + `GET /api/rag` e `POST /api/rag/acao`: ligado/autorizado, uso (montagens, com RAG, plano B), prazos estourados, cancelados, resultados tardios descartados, invalidados, desligamentos, **bloqueados** (ordens embutidas barradas, descartados por pouca relevância, na revalidação), tempos p50/p95 (montagem e recuperação), motivos de plano B, configuração efetiva. **Nenhum texto de conversa.** Botão **Desligar agora** (invalida o que está em andamento; caminho anterior segue sem contexto RAG). **Ligar** só funciona se o arquivo autoriza (senão 403). Sem token: 401. |
| **Retenção do oculto = 20 dias** | `Memory.hidden_retention_days = 20`: ao abrir o banco, o conteúdo **oculto por revogação/exclusão há mais de 20 dias** (fala, instrução/memória revogada ou substituída e o índice) é **apagado de verdade**. |
| **Identidade (C20)** | Identidade vazia/inválida **falha fechado** também no caminho antigo (leitura e escrita; `forget_all("")` não apaga o usuário padrão). `None` continua = usuário da sessão do robô (app de um usuário). `contexto.py`, `lembrar` e a recuperação exigem identidade explícita. |

### Retenção — o que muda e o que NÃO muda
- **Muda:** só o conteúdo que **já está oculto** (revogado/substituído/rejeitado/apagado) passa de "até 90 dias" para **20 dias** depois de ocultado. O banco real ainda **não tem fontes ocultas registradas** (a tabela é nova), então nada é apagado ao atualizar. Mas instruções/memórias que **já estavam revogadas, substituídas ou rejeitadas** serão marcadas como ocultas na primeira escrita depois da atualização, e os **20 dias contam a partir dessa marcação** (não da data original): o conteúdo antigo já revogado será apagado ~20 dias depois, em vez de ficar até 90.
- **NÃO muda:** o histórico **não oculto** continua com `retention_days = 90` (`NURIA_MEMORIA_DIAS`); nenhuma alteração sem aviso.
- **forget_*:** `forget_all`, `forget_session` etc. **continuam apagando de verdade e na hora** (testado, inclusive com o conteúdo oculto).

## Resultados
- Corpus do PM (C01–C20) no fluxo atual: **16 ok · 1 pendente de API (C10) · 3 não aplicáveis hoje (C13 prazo na voz, C16 troca de usuário, C17 chave de desligar em consulta de voz: Etapas 4–5) · 0 lacunas · 0 falhas de isolamento nos cenários exercitados** (não é prova de isolamento geral). Critérios desta etapa: C05, C14, C15, C18, C20 = ok.
- Custo local medido com o servidor Realtime **falso** (só o overhead do app, não a rede nem a API; `medir_rag_texto.py`): tempo da pergunta digitada até o primeiro áudio **57 ms (RAG desligado) → ~94 ms (ligado)**; montagem p50 ≈ 39 ms / p95 ≈ 46 ms, dos quais a busca em si ≈ 1,7 ms (isolada, a montagem leva ≈ 3 ms; o resto é concorrência entre threads no teste). Com RAG ligado a pergunta digitada **custa cerca de +37 ms locais** e mais tokens (um item de dados por pergunta que achou algo); desligado, nada muda. Tokens reais e latência real: **não medidos** (exigem API).

## Preparo da Etapa 4 (voz) — só desenho e medição; nada registrado
1. **Registro:** acrescentar `TOOL_LEMBRAR` a `TOOLS` **somente** com `rag_ligado`. Chamada `lembrar` → `executar_lembrar(assembler, usuario_da_sessao, consulta, cancelado)` → `function_call_output` com o JSON (dado) → continuação.
2. **Teto de 2 continuações por pergunta** (`max_continuacoes_lembrar`): hoje `_on_response_done` só limita a ferramenta cosmética (`expressar`); a funcional continua sem teto. A Etapa 4 deve contar `lembrar` por turno e, no estouro, responder o `function_call_output` com `{nada_encontrado:true, motivo_fallback:"limite"}` **sem** novo `response.create`, terminando em estado recuperável neutro (como o `NO_SPEECH_NOTICE`).
3. **Voz nunca bloqueada:** `lembrar` roda fora do caminho de captura/reprodução (thread própria, `prazo_busca_ms`); prazo estourado = resposta de plano B ("não achei") e resultado tardio descartado (já testado no montador).
4. **Cancelamento/troca:** `cancelado()` ligado a barge-in, troca de modo e de sessão; função pendente respondida com vazio; revogação em voo relê e revalida (já testado no montador; falta o teste com transporte falso na voz).
5. **Medição prevista:** nº de chamadas `lembrar` por pergunta, tempo `function_call → function_call_output → primeiro áudio` (servidor falso, relógio injetado) e, em sessão real, tokens de `usage` (`logs/uso_api.jsonl`) com × sem `lembrar`; casos C13/C14/C15/C16/C17 com transporte falso; Gemini: continua sem continuação (`continues_after_tools`), então a ferramenta precisa de um caminho próprio ali.
6. **Riscos conhecidos para a voz:** a pergunta falada só vira texto depois que a resposta já foi pedida (por isso a ferramenta); cada `lembrar` acrescenta uma volta ao servidor; modelos podem chamar `lembrar` sem necessidade (custo) — medir antes do piloto.

## Riscos e pendências
- **Mudança visível ao usuário final: nenhuma** nesta entrega (RAG desligado por padrão). Ligá-lo muda as respostas do texto digitado: **precisa de aprovação do Adryell** antes do piloto (e da decisão de público/consentimento da etapa 5).
- Qualidade de linguagem (ausência/conflito explícitos, C10), tokens reais e latência real não foram avaliados (sem API).
- A marca de conflito é heurística (falso positivo possível); o `+37 ms` do texto com RAG foi medido só com servidor falso.
- Gemini real, Raspberry Pi e a voz com `lembrar` continuam sem validação.

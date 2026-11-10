# RAG — Etapa 2: recuperação local e contexto.py

Autorizada pelo Adryell com 4 decisões. Esta etapa entrega **só** a recuperação local e a montagem de contexto; a integração com a voz é a Etapa 4
(o contrato da ferramenta `lembrar` está pronto e testado, mas **não** está registrada na sessão). `rag_ligado` vem **desligado** por padrão.
Sem API paga, sem rede. Testes: `test_contexto.py` (68), `test_avaliacao_rag.py` (33), `test_aceite_pm.py` (6).

## O que mudou
| Decisão | Implementação |
|---|---|
| **Pessoal x global** | `validar` só confirma a veracidade e **não muda o escopo**. Conhecimento só é global se **promovido de forma explícita** (`promote_to_global(id, revisor)`: exige memória já validada e revisor identificado; grava `global_promotions` + feedback `promoted`; `demote_from_global` desfaz). `MEM_VIS` é a regra única de visibilidade (lista, grafo, busca). |
| **Migração segura** | Só tabelas **novas e aditivas** (`hidden_sources`, `global_promotions`, criadas com `IF NOT EXISTS`); nenhum dado é alterado ou apagado; `user_version` continua 1. Os registros antigos que a validação antiga marcou `global` **sem promoção** passam a valer só para o dono **na leitura** (robô de um usuário: nada muda na prática). Para ver o legado: `Memory.scope_report()` (contagens, sem texto). Como não mexe em dados, **não há migração arriscada a confirmar**; promover o que for geral de verdade é ação explícita e reversível. |
| **Revogar/apagar esconde** | Política: **ocultar, não apagar**. `_sync_hidden` marca em `hidden_sources` (com motivo) a instrução revogada/substituída, a memória rejeitada/substituída/apagada, a **fala de origem** (o evento inteiro: na dúvida, esconde) e a própria ordem de "esqueça". Busca antiga, recuperação, resumo do handoff e contexto não devolvem mais. O histórico bruto segue a retenção (90 dias) e `forget_*` (apagar de verdade). |
| **Gemini** | `GeminiAdapter` agora tem **thread leitora** que põe tudo em `_out`; `recv()` só espera `_out`. Eventos gerados dentro de `send()` chegam na hora; fim da conexão vira `TransportClosed` (repetido); `close()` destrava a leitura. Limitação: validado só com o servidor simulado, **não** com o Gemini real. |
| (c) preferência nova substitui antiga | `pref_key`: mesmo assunto ("eu prefiro respostas…", nome, tratamento, "meu X favorito é…") → a antiga vira `superseded` e some (junto com a fala). Gostos diferentes ("gosto de chá" × "gosto de café") convivem. Repetir a antiga a faz voltar. |
| (e) corte de 1100 | `contexto.py` orça **cada categoria** (instruções, preferências, validado): tira o último item da maior; nunca zera uma categoria presente. Orçamentos por parte são configuráveis. |
| (b) ruído sem resposta | palavras-chave sem palavras comuns; **cobertura mínima** (padrão 0,6 das palavras-chave, radical: professor = professora); sem palavra-chave → plano B "sem_palavras". Pergunta sem resposta devolve **nada**. |
| Também | Ordem embutida ("ignore as regras", "mostre os dados de B") **não vira instrução** (`blocked_instructions`); fica só como fala/dado. Identidade vazia/ausente **falha fechado** no `contexto.py` (o caminho antigo ainda usa o usuário padrão do robô, que o app informa). Falha do índice **não desliga a memória** (`retrieve_candidates` não conta para `_fail`; plano B por LIKE). |

## contexto.py (componente único)
`ContextAssembler.montar(usuario, sessao, pergunta, handoff, recente, cancelado) -> Pacote` — itens: **(1) regras/preferências vigentes → (2) conversa recente → (3) recuperado** (dado, com fonte, data, validação e versão; sem poder de alterar regras; por último e removível depois da resposta).
- `rag_ligado=false`: devolve **exatamente** o contexto de hoje (paridade byte a byte, testada); nada é pesquisado.
- Prazo: a busca roda numa thread; passou `prazo_busca_ms` → plano B (partes 1–2) e o resultado tardio é **descartado e contado**.
- `cancelado()` (barge-in, troca de modo/usuário/sessão) → nada recuperado; `desligar()` invalida o que está em voo e o caminho antigo segue sem resíduo.
- **Sem cache**, por desenho (nada velho ressuscita). A fonte é **revalidada** (a) logo depois da busca e (b) de novo com a versão antes de consumir; se a memória mudou durante a busca, as partes fixas são relidas (revogação em voo).
- Medição por pergunta (`stats`, e `logs/contexto.jsonl` se `registrar:true`): caracteres por parte, itens, candidatos, descartes (limiar/revalidação), ms da recuperação, motivo de fallback, hash da parte fixa, versão da memória, configuração efetiva — **sem texto**.
- Configuração `rag.local.json` (ignorado pelo git; valores limitados): `rag_ligado`, `orcamento_fixo_chars` (1100), `orcamento_recente_chars` (400), `orcamento_recuperado_chars` (600), `max_itens` (4), `item_max_chars`, `prazo_busca_ms` (150), `limiar_cobertura` (0,6), `max_candidatos`, `max_continuacoes_lembrar` (2), `registrar`. São **orçamentos iniciais a calibrar com sessões reais, não metas**.

## Contrato da ferramenta `lembrar(consulta)` (Etapa 4 liga à voz)
Esquema `TOOL_LEMBRAR` (um parâmetro). `executar_lembrar(assembler, usuario_da_sessao, consulta, cancelado)` → `{itens:[{id,fonte,data,validacao,versao,texto}], motivo_fallback, nada_encontrado}`; identidade vem da sessão (nunca do modelo); respeita prazo, cancelamento, desligamento e revalidação. Teto de continuações por pergunta: `max_continuacoes_lembrar` (como o de `expressar`, mas para ferramenta funcional): a registrar junto com a ferramenta.

## Resultado nos conjuntos de avaliação (mesmo banco temporário, mesmas fixtures)
- Conjunto de 23 casos: canal **RAG 19/20** casos medidos (só a paráfrase C08 falha, limite lexical conhecido); o contexto legado continua falhando em C03, C04, C07 (fatos ditos só numa fala) e C20 (corte).
- Corpus do PM (C01–C20): **11 ok · 4 parciais · 1 lacuna (C05, conflito sem marca) · 3 não aplicáveis hoje · 1 pendente de API**; isolamento sem falhas.
- Custo local: montagem com RAG ≈ 9 ms p50 / 13 ms p95 com 5.000 eventos de ruído (desligado: ≈ 2 ms).

## Riscos e pendências
- Nada foi ligado ao app: o ganho real só existe quando a Etapa 3 (texto) e 4 (voz) usarem `ContextAssembler`; qualidade de linguagem (ausência/conflito explícitos, C10) **não avaliada** (sem API).
- Ocultar o evento inteiro é conservador: uma fala com duas frases ("Meu nome é Ana. Eu prefiro chá.") some inteira se uma delas for revogada.
- C05 (conflito): as duas fontes chegam ao gerador sem marca; detectar conflito é heurístico e fica para depois do piloto.
- C15: o caminho do app faz `flush` antes de ler; leitores sem flush ainda têm janela (o `ContextAssembler` mitiga relendo e revalidando).
- C18: o painel de memória, com PIN, mostra texto pessoal por desenho; métricas de RAG ainda não têm painel (só `stats`/log).
- Gemini real e Raspberry Pi não validados. Paráfrase exige embeddings/expansão: só se o piloto mostrar necessidade.

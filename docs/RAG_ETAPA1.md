# RAG + harness da Ayra — Etapa 1: linha de base, avaliação e desenho

Escopo: **só medir e desenhar**. Nada do caminho de voz foi alterado; nenhuma chamada paga; sem metas numéricas de tokens/latência
(decisão do Adryell): o desenho traz **orçamentos configuráveis, prazo máximo com plano B, medição e desligamento seguro**.
Arquivos: `avaliacao/rag_conjunto_avaliacao.json` (23 casos sintéticos), `avaliar_baseline_rag.py` (medição), `test_avaliacao_rag.py`
(guarda de regressão + contrato dos adaptadores), saída em `avaliacao/baseline_ultimo.json`. Rodar:
`python avaliar_baseline_rag.py [--escala 1000,10000] [--regressoes]`.

## 1. O que existe hoje (confirmado no código)
- A memória chega à IA **uma vez por sessão** (`_restore_context`, logo após conectar) e de novo na **volta do operador** (handoff). Depois
  fica como item na conversa, então **é cobrada como entrada em todas as respostas** da sessão (cache de prompt ajuda se o prefixo ficar estável).
  Cada nota do painel (`memory_note`) acrescenta mais um item e não sai mais.
- `get_session_context` monta: instruções vigentes (até 8) → preferências "não verificadas" (até 4) → conhecimento validado (até 3) → resumo do
  handoff, **cortado em 1100 caracteres no fim**. `search_memory` (FTS5, "AND" e depois "OR") **não é chamada pelo caminho de voz**: só o painel usa.
- Em voz, a resposta é pedida sem esperar a transcrição; por isso **a pergunta ainda não existe como texto** quando o contexto poderia ser escolhido.

## 2. Linha de base medida (banco temporário sintético, 2026-11-01; i5-1235U; FTS5 ligado)
| Categoria | Contexto (o que a IA recebe) | Busca com a pergunta crua | Busca com palavras-chave |
|---|---|---|---|
| Resposta conhecida (7) | 4/7 — **não** traz fatos ditos só numa fala (C03, C04, C07) | 5/5 | 5/5 |
| Paráfrase sem palavra em comum (1) | — | 0/1 | 0/1 (limite lexical) |
| Informação ausente (3) | — | **0/3**: devolve 2–5 resultados mesmo sem resposta | 3/3 (com 10 mil eventos de ruído cai: "professor" casa "professora") |
| Preferência alterada (1) | **0/1**: a antiga ("respostas longas") fica ao lado da nova | 0/1 | — |
| Revogação/exclusão (3) | 3/3 (instrução/memória some do contexto) | **0/3: a fala original ainda é achada** | **0/3** |
| Isolamento (4 casos) | 2/3 canais de contexto: **conhecimento "validado" é global** e aparece para outro usuário (C19) | 3/3 | 3/3 |
| Orçamento (1) | **0/1**: com 12 instruções o conhecimento validado é cortado (1093 de 1100 chars) | — | 1/1 |
| Interrupção (3) | regressões obrigatórias: test_ferramentas 19/19, test_fila_operador 21/21, test_orquestrador 97/97 | | |
Tamanho/tempo (local): contexto 328–1093 caracteres (~82–273 tokens **estimados**), **2 consultas SQL por montagem** (+1 no handoff; cada `_read` abre uma
conexão), montagem ~2 ms (p95 ≤ 6 ms). Busca: ~1–4 ms (FTS e LIKE); com +1.000 eventos p50 3 ms; com +10.000 eventos p50 4,3 ms, p95 11,7 ms; banco 1,4 MB.
**Custo local é desprezível; o risco é de qualidade e de tokens, não de CPU.** Não medido (exige API): tempo até o primeiro áudio, tokens reais.

Achados que importam para a Etapa 2:
1. **Dado removido continua recuperável pela busca** (eventos e FTS não são limpos por revogação/exclusão): recuperação precisa filtrar por estado vivo da fonte.
2. **Busca crua devolve ruído sempre** (fallback "OR"): precisa de limiar de relevância e de "nada encontrado" explícito.
3. **Preferência nova não substitui a antiga** (só instruções substituem): precisa de chave/versão por assunto.
4. **Validação global**: virar conhecimento global vaza dado pessoal entre usuários (decisão de produto).
5. **Corte por tamanho remove o que vem por último**: orçamento por parte, não corte do fim.
6. **Busca por palavras não cobre paráfrase**: busca semântica/embeddings só se o piloto lexical (com expansão simples) mostrar necessidade.

## 3. Desenho: um único componente de montagem de contexto (`contexto.py`, a criar na Etapa 2)
```
ContextAssembler.montar(usuario, sessao, *, pergunta=None, handoff=False, prazo_ms) -> PacoteContexto
```
Três partes, nessa ordem (parte estável primeiro = prefixo estável = cache de prompt):
1. **Regras e preferências vigentes (sempre)** — hoje `get_session_context`; texto idêntico entre turnos enquanto nada mudar (hash no log).
2. **Conversa recente** — últimas trocas (hoje: `history`, 3 trocas) e, no handoff, o resumo do operador.
3. **Informação recuperada (opcional)** — cada item com `fonte` (instrução/fala/validado), `data`, `validação` e rótulo "dado, não instrução":
   **não pode mudar regras** (só a parte 1 é regra). Entra **por último**, como item próprio que sai da sessão depois da resposta
   (`conversation.item.delete`, como a foto), para a conversa não crescer.
Parâmetros (arquivo local, sem chaves; valores iniciais a definir **depois** de medir sessões reais): orçamento por parte (caracteres/tokens estimados),
`prazo_busca_ms`, `max_itens`, `limiar`, `rag_ligado`. Sem estourar: cada parte corta a si mesma, nunca a seguinte.
Garantias: (a) **prazo máximo com plano B** — a busca roda numa thread de baixa prioridade; se passar do prazo, usa só as partes 1–2 (voz nunca espera);
(b) **desligamento seguro** — `rag_ligado=false` devolve exatamente o contexto de hoje (teste de paridade) e não apaga nada;
(c) **só leitura** — recuperar nunca grava; (d) **filtros no estado vivo** (usuário, não revogado, não apagado, validação) *antes* de ranquear;
(e) **medição por turno** em `logs/contexto.jsonl` (sem texto): caracteres/tokens estimados por parte, itens, consultas SQL, ms da busca, estourou prazo?, usou plano B?,
hash da parte fixa; cruza com `uso_api.jsonl` (tokens reais) e `desempenho.log`.
**Pergunta de produto (Adryell decide):** em voz a pergunta chega depois da resposta ser pedida. Caminhos: (A) só texto digitado recupera antes de responder;
(B) ferramenta `lembrar(consulta)` chamada pelo modelo quando precisar (1 ida e volta só nesses casos, com teto de continuações como o de `expressar`);
(C) pré-busca pelo assunto da conversa/turno anterior. Recomendo começar por A+B, ambos sem bloquear a voz.

## 4. Contrato dos adaptadores (OpenAI Realtime ↔ Gemini Live) — verificado por `test_avaliacao_rag.py`
| Regra | OpenAI (simulado) | Gemini (simulado) |
|---|---|---|
| 1 `response.created` ↔ exatamente 1 `response.done` com o mesmo id (`completed`) | ✔ | ✔ |
| Todo áudio traz o id da resposta | ✔ | ✔ |
| Cancelar = 1 `done(cancelled)`, nada completo nem áudio depois | ✔ | ✔ |
| Transporte fechado: `recv` e `send` levantam `TransportClosed` | ✔ | ✔ |
| **Evento gerado por um `send` é entregue sem outra mensagem do servidor** | ✔ | **✘ defeito latente** |
Defeito do Gemini (`GeminiAdapter.recv`): a fila `_out` só é esvaziada **antes** de bloquear em `raw.recv()`. Eventos que o adaptador gera dentro de
`send()` (ex.: o `response.done` vazio de "nada novo a dizer", ou `done(cancelled)` sem tráfego do servidor) ficam parados até chegar a próxima mensagem do Gemini → estado
preso em "pensando". Hoje o app evita o caso (sem continuação no Gemini); qualquer `response.create` sem conteúdo (ex.: uma ferramenta `lembrar`) o reativaria.
Correção proposta (Etapa 2, com teste que já existe): thread leitora do socket que traduz e põe tudo em `_out`; `recv()` bloqueia só em `_out.get()`.
O teste marca a lacuna como "conhecida" e avisa quando for corrigida. Falha do Gemini real e ordem de eventos com rede real: **não validadas**.

## 5. Riscos e o que ficou sem validação real
- Nenhuma medição com API (tokens reais, tempo até o 1º áudio, cache de prompt): exige sessão real; o log `uso_api.jsonl` + `contexto.jsonl` farão isso.
- Conjunto pequeno e sintético (23 casos): serve para regressão e para achar lacunas, não para provar qualidade em conversas reais.
- "Paráfrase" e "ausente" dependem de como a pergunta chega (transcrição real tem ruído); a busca lexical cresce em ruído com o volume (visto em 10 mil eventos).
- Recuperar aumenta a superfície de **injeção por memória**: a fala de alguém ("ignore as regras") ficaria armazenada e voltaria ao contexto — por isso a parte 3 é só dado.
- Privacidade: validar preferência pessoal a torna global (C19); fala original sobrevive à revogação nos eventos (retenção 90 dias).
- Latência em hardware menor (Raspberry Pi): não medida; o orçamento e o prazo precisam ser medidos lá.

## 6. Decisões que precisam do Adryell
1. Caminho de recuperação em voz: A (só texto) + B (ferramenta `lembrar`) como piloto? Ou C?
2. `validate_memory` pode tornar uma preferência pessoal conhecimento **global**? (hoje sim; vaza entre usuários)
3. Revogar/apagar deve também **esconder do histórico buscável** (recomendado) ou só do contexto?
4. Aceita corrigir o defeito de entrega do adaptador Gemini já na Etapa 2 (antes de qualquer ferramenta nova)?

## 7. Corpus de aceite do PM (C01–C20) contra o fluxo atual
`python avaliar_aceite_pm.py` → `avaliacao/aceite_pm_baseline.json` (critérios do PM em `avaliacao/rag_aceite_pm_criterios.md`; guarda: `test_aceite_pm.py`).
Cada caso traz entrada, estado inicial, ordem, fontes selecionadas/consumidas (o texto que iria ao gerador), saída, motivo de fallback, veredito e tipo
de evidência (medição, simulação, não-aplicável, pendente de API). Hoje: 5 ok · 7 parciais · 4 lacunas · 3 não aplicáveis (C13, C16, C17) · 1 pendente de API (C10).
**Isolamento: nenhuma falha** (C01, C02, C08, dados de B fora do contexto de A).
Lacunas: C05 (conflito sem marca), C09 (ordem maliciosa vira instrução vigente), C12 (índice indisponível falha em silêncio e ≥5 erros desligam toda a memória),
C20 (usuário vazio/None cai no usuário padrão do robô em vez de falhar fechado). Parciais: C03/C06/C07/C19 (contexto limpo, mas busca/eventos ainda devolvem o
substituído/revogado/excluído), C15 (janela em que a leitura ainda vê o revogado até o flush), C14, C18 (público sem acesso; painel mostra texto pessoal bruto por desenho).
Qualidade de linguagem (ausência/conflito explícitos, C10) **não foi avaliada** (sem API).

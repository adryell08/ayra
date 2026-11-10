# Ayrabrain — mapa das instruções e memórias da Ayra

Página privada do painel do operador: `https://<ip-do-robô>:8765/ayrabrain` (mesmo endereço da sala do operador e do
painel `/memoria`). Entra com o **mesmo PIN** (Ajustes → Conexões); quem já entrou em `/memoria` já está dentro.
Nada disso aparece na tela do robô, e a página fechada não custa nada (sem quadro, sem consulta, sem API).

## O que mostra
- **Pontos**: instruções vigentes (azul) e memórias/preferências (verde = validada, laranja = não verificada) do usuário
  atual, mais o conhecimento global validado. Só dados que já existem em `memoria.db`; nada é inferido.
- **Ligações**: só as que uma pessoa criou aqui. Tipos: *relacionado*, *depende*, *contradiz*, *exemplo*.
- Busca, pan/zoom (arrastar, roda, pinça, setas/+/− com o mapa focado), clique → detalhes, duplo clique ou
  **Ver vizinhança** → o ponto e seus vizinhos diretos, **Voltar ao mapa**. “Lista de pontos” é a alternativa acessível.
- **Conectar a…** (escolha na lista ou clicando no outro ponto) e **Desligar**; ambos com **Desfazer**.

## Como funciona (para manter leve)
- `Memory.brain_graph()` devolve uma página (≤ 80 nós por padrão, ≤ 150 no máximo) e só as ligações entre eles; o tamanho
  é limitado por usuário (≤ 500 ligações, ≤ 24 por ponto). Consulta numa thread de baixa prioridade (`operador.py`),
  nunca no laço de áudio/3D. A página só pergunta “mudou?” a cada 10 s (resposta mínima) com a aba visível.
- Ligações ficam na tabela `brain_links` (criada sozinha, sem mexer na versão do esquema; `memory_links` segue sem uso).
  Criar valida formato, relação, existência e dono (mesmo usuário); desligar é por id; apagar/rejeitar memória,
  revogar instrução ou “esquecer tudo” limpa as ligações; editar uma instrução leva as ligações para o texto novo.
- Desenho em canvas 2D, física simples que **para sozinha** (~4 s) e só redesenha quando algo muda.
- API: `GET /api/ayrabrain?limit&offset&q&focus&v` e `POST /api/ayrabrain/acao` (`ligar`, `desligar`), todas atrás do
  token (`X-Token`) e do limite de 20 pedidos/s do painel de memória.

## Testar
`python test_ayrabrain.py` (banco temporário, porta de teste 18783; não toca em `logs/memoria.db`).
Demo com dados de exemplo: ver o roteiro no relatório da tarefa ou rode o app e abra `/ayrabrain`.

## Fora desta etapa
Ligações não entram no prompt da IA (custo de tokens); sugestão automática de ligações; edição de texto dos pontos pelo
mapa; layout persistido; validação com muitos milhares de pontos e em celular real.

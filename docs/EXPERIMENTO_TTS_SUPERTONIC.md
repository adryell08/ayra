# Experimento isolado: voz local por TTS (Supertonic 3 int8 via sherpa-onnx) — EXECUTADO, fora do app

Resultado em uma linha: **tecnicamente funciona em português, mas é pesado para o padrão da Ayra (pico ~450 MB, 1–2 s até o 1º som) →
não integrar como voz padrão; manter como candidato opcional, só para frases digitadas do operador / respostas em texto, se o Adryell
ouvir e aprovar a qualidade e aceitar a licença dos pesos.** A fala ao vivo e o streaming da IA/operador não foram tocados.

## Fonte e licença (conferidas, não presumidas)
- Artefatos: `huggingface.co/csukuangfj2/sherpa-onnx-supertonic-3-tts-int8-2026-05-11`, revisão `cca5a0e6c96e1d2c720986bf7e75fcc81dee3ae4`
  (baixados em 2026-10-30; 9 arquivos, **139 MB**, tamanhos e SHA-256 conferidos com o índice do Hugging Face; ver `MANIFESTO.json`).
- Código/exemplos: **MIT** (arquivo `LICENSE` da pasta, © Supertone Inc.). Pesos do modelo oficial `Supertone/supertonic-3`:
  **OpenRAIL-M** (BigScience Open RAIL-M; cópia salva como `LICENSE_pesos_OpenRAIL-M.txt`). É licença de uso responsável: tem restrições de
  uso que passam adiante para quem redistribuir/usar. **Precisa de leitura e aceite do Adryell antes de qualquer uso real.**
- Runtime: `sherpa-onnx 2.0.1` (Apache-2.0), roda do PyPI como roda binária (`cp314`, 2,2 MB), instalada em venv isolado. Nenhum código remoto
  foi executado além de pacotes de terceiros já conhecidos (sherpa-onnx, numpy, psutil); sem o runtime interno do OpenBot.
- Onde está: `C:\Users\adrye\OpenBot\Agents\agent-2de3b3d3-…\experimentos\supertonic3\` (fora do repositório; pesos nunca entram no git).

## O que é / não é
TTS texto → fala, 44,1 kHz, 10 vozes predefinidas (F1–F5, M1–M5), português aceito (`lang="pt"` muda a saída: 8,70 s vs 8,09 s com "en" ou sem idioma).
**Não** converte o microfone ao vivo. Cobre respostas textuais da IA e frases digitadas do operador. Texto sintético; nenhuma voz pessoal.

## Medidas (i5-1235U, 10 núcleos/12 threads, 16,8 GB, Windows 11, só CPU, Python 3.14; 15 frases × 3 repetições após aquecimento)
| Config | curta (2,7 s de áudio) p50 / p95 | média (7,4 s) p50 / p95 | longa (17 s) p50 / p95 | CPU | RTF p50 |
|---|---|---|---|---|---|
| 1 thread, 4 passos | 0,65 / 0,76 s | 1,56 / 1,79 s | 3,66 / 3,78 s | ~98 % (1 núcleo) | 0,20–0,25 |
| 2 threads, 4 passos | 0,82 / 2,82 s | 1,28 / 1,57 s | 2,70 / 3,78 s | 290–420 % | 0,16–0,31 |
| 4 threads, 4 passos | 1,58 / 2,20 s | 3,46 / 4,19 s | 3,78 / 6,15 s | 570–710 % | 0,22–0,60 |
| 2 threads, 2 passos | 0,33 / 0,48 s | 0,76 / 1,30 s | 1,51 / 1,59 s | 370–490 % | 0,09–0,14 |
- Carga do modelo: 0,65–1,35 s (4,2 s na 1ª vez em disco frio); 1ª geração 0,3–2,0 s. **RAM: 75–80 MB → ~245 MB após carregar → pico ~450 MB.**
- O sherpa só devolve o áudio no fim de cada chamada: "1º som" = síntese da 1ª **frase**. Dividindo a resposta em frases: p50 ≈ 2,0 s (1 thread, 4 passos),
  1,5 s (2 threads), 1,1 s (1 thread, 2 passos); p95 chega a 2–9 s na máquina ocupada.
- Resultados **ruidosos**: a máquina rodava outros agentes; mais threads não ajudou (CPU híbrida P+E, 4 threads ficou pior que 2). Os números dizem a ordem de grandeza, não um benchmark limpo.
- Menos passos (2) acelera ~2×, mas **não avaliei a qualidade**: ninguém ouviu as 12 amostras (`amostras/`); só conferi duração plausível, sem silêncio e sem estouro grosseiro.

## Linha de base: voz local do Windows (SAPI, Microsoft Maria PT-BR, como o app usa)
Síntese p50 **0,09 s** (curta) / 0,10 s (média) / 0,13 s (longa), p95 ≤ 0,20 s; RTF 0,01–0,03; processo ~48 MB (pico 66 MB). Voz clássica, mas ~10× mais rápida e ~7× mais leve.

## Decisão
- **Descartar como voz padrão e na fala ao vivo**: contraria "pouca memória e fluidez" (pico 450 MB e ≥ 1 s de espera por frase vs 0,1 s do SAPI) e a IA
  hoje já fala pelo áudio nativo da Realtime sem esperar texto.
- **Candidato opcional** (fora do caminho crítico): voz neural para frases **digitadas do operador** (hoje SAPI) e para respostas só em texto,
  carregada sob demanda num processo separado e descarregada depois, com o mesmo contrato de segurança já existente (perfil explícito, sem plano B
  silencioso para outra voz, diagnóstico privado, aviso público neutro, desligado por padrão).
- Antes de integrar: (1) o Adryell ouve as amostras e escolhe/rejeita; (2) lê e aceita a OpenRAIL-M; (3) repetir a medição em máquina quieta e no Raspberry Pi 4
  (provavelmente inviável por RAM/CPU); (4) decidir se vale ~140 MB de disco e ~450 MB de RAM por esse ganho de naturalidade.

## Como repetir
No venv da pasta do experimento: `python bench_supertonic.py` (config completa), `python bench_primeira_frase.py` (1ª frase) e, com o Python do projeto,
`python bench_sapi.py` (SAPI). Saídas: `RESULTADOS*.json`.

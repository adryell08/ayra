# Vídeo da sala do operador a 30 quadros/s

Branch: `otimizacao-webrtc-30fps` (a `main` não foi alterada). Estado: **simulado no container (2 CPUs, sem GPU, sem câmera real)**; medir no PC do robô antes de usar numa apresentação.

## Gargalos encontrados (caminho: câmera → captura → trilha WebRTC → codificação VP8 → rede → navegador)
| Etapa | Antes | Problema |
|---|---|---|
| Captura (`tracker.py`) | entregava à sala no máximo **16 quadros/s** (`1/16`) e fazia `frame.copy()` a cada entrega | teto duro: 30 reais eram impossíveis; cópia de 640×480×3 (~0,9 MB) por quadro |
| Trilha (`operador.py`) | relógio fixo por incremento; atrasos geravam rajada de recuperação; `pts` somado por passo; o mesmo quadro podia ser recodificado; hop de thread por quadro | latência acumulada, jitter, CPU gasto em quadro repetido |
| Opções de taxa | 5, 10, 15 | sem 30 |
| Medição | só a taxa escolhida | nada de fps real, idade do quadro, CPU, memória |
| 3D (`corpo3d.py`) | alocava matrizes e um `bytes` de leitura a cada quadro | alocação por quadro |

## O que mudou
- **Captura:** `latest_frame_info()` devolve (quadro, sequência, instante) **por referência** (sem cópia: `cap.read()` já devolve array novo e ninguém o altera). A câmera só guarda quadros na taxa pedida pela sala (`set_operator_fps`, até 60) e pede 30 fps à webcam (`CAP_PROP_FPS`).
- **Trilha:** relógio fixo com **descarte de atraso** (atrasado mais de um período: refaz o relógio, sem rajada); quadro igual ao anterior **não é recodificado** (espera até 1,5 período pelo novo e só então repete, para manter o fluxo); `pts` em tempo real; sem hop de thread; redução para 640 de largura só quando necessário; dimensões pares.
- **Métricas** (`VideoStats`, a cada conexão): fps real, idade do quadro (mediana e p95), quadros descartados e repetidos, relógios refeitos, CPU do processo e memória. Chegam ao painel pela telemetria (`video`) e aparecem no topo da sala: `AO VIVO · 29/30 FPS · 40 ms` (dica com todos os números).
- **Ajuste adaptativo** (`AdaptiveFps`): desce um degrau (30→15→10→5) se o fps real ficar abaixo de 75 % do alvo ou o quadro chegar com mais de 400 ms em 2 avaliações seguidas; sobe um degrau depois de 6 avaliações boas (≥ 95 % e < 150 ms). Nunca passa do teto escolhido em Ajustes → Controle remoto → Vídeo (5, 10, 15 ou **30**).
- **3D:** matrizes e buffer de leitura reutilizados (`read_into`).
- **Preservado:** PIN (com limite de tentativas), HTTPS, um operador por vez, reconexão (6 s de ICE + 20 s de tolerância), IA, RAG, memória, áudio, gestos, expressões, rostos e teleoperação. O padrão continua **10 quadros/s**.

## Resultados (container; `python medir_video_operador.py`, dados em `avaliacao/video_operador.json`)
Câmera simulada 640×480 a 30 quadros/s, cliente WebRTC em outro processo, VP8 por software.

| Cenário | fps no cliente | fps no servidor | idade do quadro (mediana / p95) | CPU do robô | memória |
|---|---|---|---|---|---|
| 15 fps | 15,0 | 15,0 | 30 / 34 ms | 14 % de 1 núcleo | 149 MB |
| 30 fps | 30,0 | 29,9 | 1 / 3 ms | 21 % | 148 MB |
| 30 fps, adaptativo | 29,8 | 29,7 | 5 / 9 ms | 21 % | 148 MB |
| 30 fps, 2 núcleos ocupados por outros programas | 21,6 | 22,2 | 3 / 34 ms | 17 % | 155 MB |
| idem, adaptativo | 24,6 | 24,6 | 5 / 47 ms | 19 % | 155 MB |

Leitura: com CPU livre, 30 reais custam ~7 pontos de CPU a mais que 15 e não aumentam a memória. Sob disputa de CPU o fps cai, mas o quadro **não envelhece** (idade p95 < 50 ms), porque atrasos são descartados em vez de enfileirados. O adaptativo só intervém abaixo de 75 % do alvo, por isso nos cenários acima ele manteve 30 (houve 22–25 reais).

## Limites e o que falta medir
- **Não medido no PC do robô** (Windows, Intel Iris Xe) nem com webcam real: a câmera pode entregar menos de 30 (pouca luz, formato YUY2) e o desenho 3D/áudio/IA disputam CPU e GPU. Os logs do PC mostravam 6–12 fps de desenho durante conversa; o vídeo para o operador corre em threads separadas, mas divide os mesmos núcleos.
- A codificação é VP8 por software (aiortc). Codificação H.264 por hardware reduziria CPU, mas depende do driver e não foi tentada.
- No navegador do operador não foi medido o fps de exibição nem a latência de ponta a ponta (câmera → tela); `idade_ms` mede só até o envio.
- Para ligar: Ajustes → Controle remoto → Vídeo, tocar até **30**. Se travar, o adaptativo reduz sozinho; o painel mostra a taxa real.

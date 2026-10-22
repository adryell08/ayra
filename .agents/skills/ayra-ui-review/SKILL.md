---
name: ayra-ui-review
description: Revisar visualmente as telas do Robô-Olhos com cenas simuladas, verificando leitura, foco, estados e responsividade sem capturar imagens de webcam.
---

Gere prévias com fonte simulada: tela padrão, ajustes, ajuda, erro e diagnóstico. Inspecione as imagens nas menores dimensões suportadas e em uma janela ampla, nos temas claro, escuro e alto contraste.

Confira cortes de texto, sobreposição de controles, foco de teclado, indicador de câmera e hierarquia dos olhos. Exercite rolagem até o último item e fechamento de modais por mouse e Escape. Meça contraste das superfícies compostas e tamanho dos alvos; não valide só constantes.

Corrija o problema observado e regenere apenas as cenas afetadas. Rode o selftest do Python antes de integrar. Na Web, use testes do núcleo e inspeção em navegador; declare separadamente o que depende de hardware real.

Adaptação local do fluxo de crítica visual pesquisado em [frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design); sem copiar o pacote upstream. As regras e o visual do usuário prevalecem.

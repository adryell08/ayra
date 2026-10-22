# Nuria no painel de LEDs (Arduino Uno + MAX7219 32×8)

O painel mostra os mesmos olhos da tela (olhar, piscadas, emoções, choro), redesenhados para 32×8 — sem a boca,
que não cabe em 8 linhas. Layout **16×8 | 16×8**: cada olho tem a sua metade, fica centralizado nela em repouso e
anda dentro dela ao olhar para os lados. A interface na tela não muda.

## 1. Ligações (5 fios, direto do módulo ao Arduino — sem protoboard)

Use o conector da **entrada** do painel: o lado em que as setas impressas no verso apontam **para dentro** (“IN”).

| Módulo (verso) | Arduino Uno | Função |
|---|---|---|
| **VCC** | **5V** (barra POWER) | Alimentação |
| **GND** | **GND** (qualquer um) | Terra |
| **DIN** | **D11** | Dados (MOSI do SPI) |
| **CS**  | **D10** | Seleção do chip |
| **CLK** | **D13** | Relógio (SCK do SPI) |

```
 Arduino Uno                               Painel 4× MAX7219 (verso, conector IN)
 ┌────────────────────┐
 │ POWER  5V  ●───────┼───────────────────► VCC
 │        GND ●───────┼───────────────────► GND
 │ DIGITAL 11 ●───────┼───────────────────► DIN
 │         10 ●───────┼───────────────────► CS
 │         13 ●───────┼───────────────────► CLK
 │   [USB] ───────────┼──► PC (Nuria)
 └────────────────────┘
```

- Siga as **letras impressas no módulo**, não as cores dos fios.
- Não use os pinos 0 e 1 (RX/TX): eles são a comunicação USB com o PC.
- Faça as ligações com o USB desconectado. Não ligue VCC no 3,3 V nem inverta VCC/GND.
- Alimentação: a USB do PC aguenta os olhos em brilho baixo (padrão 2 de 15). Para brilho alto ou o painel
  todo aceso, use fonte externa de 5 V / 2 A no VCC/GND do módulo, com o GND também ligado ao GND do Arduino.

## 2. Instalar no PC (uma vez)

1. **Driver USB**: o Uno original funciona direto no Windows. Clones com chip **CH340** às vezes pedem o driver
   CH341SER (fabricante WCH). Confira no Gerenciador de Dispositivos → *Portas (COM e LPT)*.
2. **Gravar o firmware** (escolha um):
   - Dois cliques em **`gravar_arduino.bat`** (instala o `arduino-cli` via winget, detecta a porta e grava); ou
   - **Arduino IDE 2** (arduino.cc): abra `arduino/nuria_leds/nuria_leds.ino`, placa *Arduino Uno*, porta COM, **Carregar**.
   Nenhuma biblioteca extra é necessária. Ao terminar, o painel acende duas “pupilas”.
3. **`pyserial`**: o `iniciar.bat` instala sozinho (já está no `requirements.txt`).

## 3. Usar e calibrar

1. Feche o Monitor Serial da IDE (ele ocupa a porta). Abra a Nuria.
2. **Ajustes (F10) → Arduino → Painel LED 32×8**: ligar. O status mostra “conectado · COMx”.
3. Ligue o **Padrão de teste**. O correto, olhando o painel de frente na horizontal:
   ponto aceso **no canto superior esquerdo** e seta **apontando para a direita**; na base, 1, 2, 3 e 4 pontinhos
   da esquerda para a direita. Se não estiver assim, ajuste (cada toque aplica na hora):
   - **Entrada dos fios**: módulos na ordem trocada (4-3-2-1);
   - **Girar cada módulo**: cada bloco 8×8 girado;
   - **Espelhar módulos**: cada bloco espelhado;
   - **Painel de cabeça para baixo**: tudo invertido.
4. Desligue o padrão de teste. Ajuste **Brilho** e **Tamanho dos olhos**. Tudo fica salvo em `arduino.local.json`.

## 4. Como funciona

PC: a cada quadro (30/s) o rosto do mesmo instante é redesenhado numa grade própria, recortado em 32×8 e
convertido para os 4 módulos → 35 bytes pela USB (115200 baud). Arduino: só repassa ao MAX7219 por SPI.
Se o app fechar ou travar, o painel apaga em 3 s. Cabo puxado: a Nuria avisa e reconecta sozinha.

## 5. Problemas comuns

| Sintoma | Causa provável | Solução |
|---|---|---|
| Só a **linha de cima** acende (os olhos viram um traço que acompanha o olhar) | Ruído no SPI zerou o registrador “scan limit” do MAX7219 | Grave o firmware v2 (`gravar_arduino.bat`): ele reconfigura o painel a cada quadro e usa SPI a 1 MHz. Prefira fios curtos |
| Status “firmware antigo” | Arduino com a versão 1 | Grave de novo |
| Desenho “embaralhado” | Montagem diferente da padrão | Padrão de teste + ajustes de montagem (seção 3) |
| Painel todo aceso / Arduino reiniciando | Corrente demais na USB | Brilho baixo ou fonte externa de 5 V (seção 1) |


// Nuria — painel de LEDs 32x8 (4x MAX7219 encadeados) controlado pelo PC via USB.
// Não precisa de biblioteca extra (SPI já vem com a IDE do Arduino).
//
// Ligações (Arduino Uno  ->  módulo, lado da ENTRADA / "IN"):
//   5V  -> VCC     GND -> GND     D11 -> DIN     D10 -> CS     D13 -> CLK
//
// Protocolo (115200 baud): 0xA5 <cmd> <dados> <xor dos dados>
//   cmd 0x01: 32 bytes (módulo 0 = o mais perto da entrada, linhas 0..7)
//   cmd 0x02: 1 byte de brilho (0..15)
//   cmd 0x03: sem dados; responde "NURIA-LED 2 4"
#include <SPI.h>

const uint8_t PIN_CS = 10;
// Versão 2: reconfigura o MAX7219 a cada quadro e usa SPI a 1 MHz.
const uint8_t MODULES = 4;
const uint8_t FRAME_BYTES = MODULES * 8;

uint8_t frame[FRAME_BYTES];
uint8_t buf[FRAME_BYTES];
unsigned long lastFrameAt = 0;

void sendAll(uint8_t reg, uint8_t value) {
  digitalWrite(PIN_CS, LOW);
  for (uint8_t m = 0; m < MODULES; m++) { SPI.transfer(reg); SPI.transfer(value); }
  digitalWrite(PIN_CS, HIGH);
}

uint8_t intensity = 2;

// Reafirma a configuração a cada quadro. Com fios jumper longos, um bit corrompido no SPI pode gravar
// por engano o registrador "scan limit" (0x0B) com 0 — o MAX7219 passa a mostrar só a 1ª linha.
// Reenviar os registradores de controle desfaz isso no quadro seguinte.
void configure() {
  sendAll(0x0F, 0);          // teste do display: desligado
  sendAll(0x09, 0);          // sem decodificação BCD
  sendAll(0x0B, 7);          // varre as 8 linhas
  sendAll(0x0A, intensity);  // brilho
  sendAll(0x0C, 1);          // fora do modo desligado
}

void show() {
  configure();
  for (uint8_t row = 0; row < 8; row++) {
    digitalWrite(PIN_CS, LOW);
    // O primeiro par enviado vai para o módulo mais distante da entrada.
    for (int8_t m = MODULES - 1; m >= 0; m--) {
      SPI.transfer(row + 1);
      SPI.transfer(frame[m * 8 + row]);
    }
    digitalWrite(PIN_CS, HIGH);
  }
}

void hello() { Serial.print(F("NURIA-LED 2 ")); Serial.println(MODULES); }

void setup() {
  pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_CS, HIGH);
  SPI.begin();
  // 1 MHz: margem para fios jumper de 20–30 cm (o MAX7219 aceita até 10 MHz em fios curtos).
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  configure();
  memset(frame, 0, sizeof(frame));
  // Sinal de vida: duas "pupilas" acesas até o PC conectar.
  frame[3] = frame[4] = 0x18;
  frame[MODULES * 8 - 5] = frame[MODULES * 8 - 4] = 0x18;
  show();
  Serial.begin(115200);
  hello();
}

bool readBytes(uint8_t *dst, uint8_t n) {
  unsigned long t0 = millis();
  uint8_t got = 0;
  while (got < n) {
    if (Serial.available()) dst[got++] = Serial.read();
    else if (millis() - t0 > 50) return false;   // pacote incompleto: descarta
  }
  return true;
}

void loop() {
  if (!Serial.available()) {
    // Sem quadros por 3 s (app fechado/travado): apaga para não deixar o rosto congelado.
    if (lastFrameAt && millis() - lastFrameAt > 3000) { memset(frame, 0, sizeof(frame)); show(); lastFrameAt = 0; }
    return;
  }
  if (Serial.read() != 0xA5) return;
  uint8_t cmd;
  if (!readBytes(&cmd, 1)) return;
  uint8_t n = cmd == 0x01 ? FRAME_BYTES : (cmd == 0x02 ? 1 : 0);
  if (cmd < 0x01 || cmd > 0x03) return;
  uint8_t check;
  if (n && !readBytes(buf, n)) return;
  if (!readBytes(&check, 1)) return;
  uint8_t x = 0;
  for (uint8_t i = 0; i < n; i++) x ^= buf[i];
  if (x != check) return;
  if (cmd == 0x01) { memcpy(frame, buf, FRAME_BYTES); show(); lastFrameAt = millis(); }
  else if (cmd == 0x02) { intensity = buf[0] & 0x0F; sendAll(0x0A, intensity); }
  else hello();
}

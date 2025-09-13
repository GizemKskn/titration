#include <Arduino.h>

/* ===================== DONANIM HARİTASI (DEĞİŞMEZ) =====================
 * HX711  : DOUT = A7 (analog okunacak), SCK = A5 (dijital çıkış)
 * Stepper1: STEP=11, DIR=10, EN=A0
 * Stepper2: STEP=8,  DIR=9,  EN=A1
 * Stepper3: STEP=6,  DIR=4,  EN=A2
 * Air motor: D5,  Water motor: D7,  Solenoid: D2,  Camera: D3
 * pH sensörü: PH_PIN (varsayılan A3; PCB’de farklıysa yalnızca bu satırı değiştir)
 * ====================================================================== */

#define HX_DOUT  A7
#define HX_SCK   A5

// ---- pH sensörü ----
#define PH_PIN     A12          // PCB’de pH hangi analog pine geldiyse onu yaz
#define PH_OFFSET  (-1.00f)    // kalibrasyon ofsetin

// ---- Step & IO pinleri ----
const int stepPin1 = 11, dirPin1 = 10, enablePin1 = A0;
const int stepPin2 = 8,  dirPin2  = 9,  enablePin2 = A1;
const int stepPin3 = 6,  dirPin3  = 4,  enablePin3 = A2;

const int airMotorPin   = 5;
const int cameraPin     = 3;
const int waterMotorPin = 7;
const int selenoid      = 2;

// =================== Seri Çıkış Kontrolü ===================
bool VERBOSE = false;                 // default sessiz
inline void vlog(const char* s){ if (VERBOSE) Serial.println(s); }

// =================== HX711 (kütüphanesiz) ==================
// gram = (raw - tare_offset) / scale_factor
long  tare_offset  = 0;              // TARE ile ayarlanır (açılışta ham okunup atanır)
float scale_factor = 936.57f;        // kendi kalibrasyonunla güncelle

static inline void hxSckHigh(){ digitalWrite(HX_SCK, HIGH); }
static inline void hxSckLow() { digitalWrite(HX_SCK, LOW);  }

// HX711 hazır (DOUT LOW) kontrolü – A7 analog olduğundan eşik kullanıyoruz
bool hxReady() {
  int v = analogRead(HX_DOUT);       // 0..1023
  return v < 512;                    // ~LOW
}

// 24‑bit ham okuma (Channel A, gain 128 → 1 ekstra clock)
long hxReadRaw() {
  // hazır olana kadar bekle (timeout 1 sn)
  unsigned long t0 = millis();
  while (!hxReady()) {
    if (millis() - t0 > 1000) return 0;   // zaman aşımı
    delay(1);
  }

  long value = 0;
  for (int i = 0; i < 24; i++) {
    hxSckHigh();
    delayMicroseconds(3);

    // DOUT seviyesini oku (analog eşiğe göre dijitalleştir)
    int a = analogRead(HX_DOUT);
    uint8_t bit = (a > 512) ? 1 : 0;
    value = (value << 1) | bit;

    hxSckLow();
    delayMicroseconds(3);
  }

  // Gain seçimi (A,128) için 1 ekstra clock
  hxSckHigh(); delayMicroseconds(3);
  hxSckLow();  delayMicroseconds(3);

  // 24‑bit iki's tamamını 32‑bit signed’a genişlet
  if (value & 0x800000L) value |= ~0xFFFFFFL;
  return value;
}

float getWeight() {
  const int N = 5;     // ortalama (gürültüyü azaltır)
  long sum = 0;
  for (int i = 0; i < N; i++) sum += hxReadRaw();
  long raw_avg = sum / N;
  float w = (raw_avg - tare_offset) / scale_factor;
  return w;
}

// =================== pH Ölçümü ===================
float getPH() {
  const int N = 20;
  int buf[N];
  for (int i = 0; i < N; i++) { buf[i] = analogRead(PH_PIN); delay(8); }

  // sıralama (küçükten büyüğe)
  for (int i = 0; i < N-1; i++)
    for (int j = i+1; j < N; j++)
      if (buf[i] > buf[j]) { int t = buf[i]; buf[i] = buf[j]; buf[j] = t; }

  // ortadaki 10 değerin ortalaması
  long sum = 0;
  for (int i = 5; i < 15; i++) sum += buf[i];
  float avg = (float)sum / 10.0f;

  float v  = avg * (5.0f / 1023.0f); // volt
  float ph = 3.5f * v + PH_OFFSET;   // basit doğrusal yaklaşım
  return ph;
}

// =================== Step Motor Sürüşü ===================
void moveStepper(int pin, int directionPin, int enablePin, long steps) {
  digitalWrite(enablePin, LOW);
  digitalWrite(directionPin, steps >= 0 ? HIGH : LOW);

  unsigned long n = (unsigned long)abs(steps);
  for (unsigned long k = 0; k < n; k++) {
    digitalWrite(pin, HIGH);  delayMicroseconds(800);
    digitalWrite(pin, LOW);   delayMicroseconds(800);
  }
  digitalWrite(enablePin, HIGH);
  delay(40);
  Serial.println("DONE");      // tek satır yanıt
}

// =================== Komut Yorumlayıcı ===================
void executeCommand(String command) {
  command.trim();

  // ----- yönetim -----
  if (command == "VERBOSE_ON")  { VERBOSE = true;  Serial.println("OK"); return; }
  if (command == "VERBOSE_OFF") { VERBOSE = false; Serial.println("OK"); return; }
  if (command == "PING")        { Serial.println("PONG"); return; }

  // ----- step / IO -----
  if (command.startsWith("MOVE1")) {
    long steps = command.substring(6).toInt();
    moveStepper(stepPin1, dirPin1, enablePin1, steps); return;
  }
  if (command.startsWith("MOVE2")) {
    long steps = command.substring(6).toInt();
    moveStepper(stepPin2, dirPin2, enablePin2, steps); return;
  }
  if (command.startsWith("MOVE3")) {
    long steps = command.substring(6).toInt();
    moveStepper(stepPin3, dirPin3, enablePin3, steps); return;
  }

  if (command == "AIR_ON")   { digitalWrite(airMotorPin, HIGH);  Serial.println("DONE"); return; }
  if (command == "AIR_OFF")  { digitalWrite(airMotorPin, LOW);   Serial.println("DONE"); return; }
  if (command.startsWith("AIR_DUR")) {
    long ms = command.substring(8).toInt();
    digitalWrite(airMotorPin, HIGH); delay(ms);
    digitalWrite(airMotorPin, LOW);  Serial.println("DONE"); return;
  }

  if (command == "WATER_ON")  { digitalWrite(waterMotorPin, HIGH); Serial.println("DONE"); return; }
  if (command == "WATER_OFF") { digitalWrite(waterMotorPin, LOW);  Serial.println("DONE"); return; }
  if (command.startsWith("WATER_DUR")) {
    long ms = command.substring(10).toInt();
    digitalWrite(waterMotorPin, HIGH); delay(ms);
    digitalWrite(waterMotorPin, LOW);  Serial.println("DONE"); return;
  }

  if (command == "VALVE_ON")  { digitalWrite(selenoid, HIGH); Serial.println("DONE"); return; }
  if (command == "VALVE_OFF") { digitalWrite(selenoid, LOW);  Serial.println("DONE"); return; }
  if (command.startsWith("VALVE_DUR")) {
    long ms = command.substring(10).toInt();
    digitalWrite(selenoid, HIGH); delay(ms);
    digitalWrite(selenoid, LOW);  Serial.println("DONE"); return;
  }

  if (command.startsWith("COKME_DUR")) { long ms = command.substring(10).toInt(); delay(ms); Serial.println("DONE"); return; }

  if (command == "CAMERA_TRIG") {
    digitalWrite(cameraPin, HIGH); delay(100);
    digitalWrite(cameraPin, LOW);  Serial.println("DONE"); return;
  }

  // ----- ölçümler & kalibrasyon -----
  if (command == "WEIGHT_MEASURE") {
    float w = getWeight();
    Serial.print("Weight: "); Serial.println(w, 3);  // Python tarafı "Weight: " bekliyor
    return;
  }
  if (command == "RAW_READ")       { long r = hxReadRaw();  Serial.print("RAW:");    Serial.println(r);   return; }
  if (command == "TARE")           { long r = hxReadRaw();  tare_offset = r;         Serial.print("TARE:"); Serial.println(tare_offset); return; }
  if (command.startsWith("SET_SCALE")) {
    float s = command.substring(9).toFloat();
    if (s > 0.001f) { scale_factor = s; Serial.print("SCALE:"); Serial.println(scale_factor, 3); }
    else Serial.println("ERR");
    return;
  }
  if (command.startsWith("SET_TARE")) {
    long t = command.substring(8).toInt();
    tare_offset = t; Serial.print("TARE:"); Serial.println(tare_offset); return;
  }
  if (command == "PH_MEASURE") {
    float p = getPH();
    Serial.print("PH: "); Serial.println(p, 2);      // Python tarafı "PH: " bekliyor
    return;
  }

  // ----- test akışını kapatma için uyumluluk -----
  if (command == "COMPLETE_TEST") {
    // Herhangi bir sayaç tutmuyoruz; UI beklemesin diye DONE döndürüyoruz
    Serial.println("DONE");
    return;
  }

  // tanınmayan komut
  Serial.println("ERR");
}

// =================== setup / loop ===================
void setup() {
  Serial.begin(9600);

  // HX711 SCK
  pinMode(HX_SCK, OUTPUT);
  hxSckLow();

  // Step ve IO pinleri
  pinMode(stepPin1, OUTPUT); pinMode(dirPin1, OUTPUT); pinMode(enablePin1, OUTPUT);
  pinMode(stepPin2, OUTPUT); pinMode(dirPin2, OUTPUT); pinMode(enablePin2, OUTPUT);
  pinMode(stepPin3, OUTPUT); pinMode(dirPin3, OUTPUT); pinMode(enablePin3, OUTPUT);

  pinMode(airMotorPin, OUTPUT);
  pinMode(waterMotorPin, OUTPUT);
  pinMode(selenoid, OUTPUT);
  pinMode(cameraPin, OUTPUT);

  digitalWrite(enablePin1, HIGH);
  digitalWrite(enablePin2, HIGH);
  digitalWrite(enablePin3, HIGH);
  digitalWrite(selenoid, LOW);
  digitalWrite(cameraPin, LOW);

  // İlk kaba TARE (boş kefedeyken)
  tare_offset = hxReadRaw();
  // Başlangıçta serial'a mesaj basmıyoruz (UI kasmasın)
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    executeCommand(cmd);
  }
}

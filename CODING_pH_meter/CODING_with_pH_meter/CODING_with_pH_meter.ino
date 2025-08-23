#include <Arduino.h>

// ================== HX711 (kütüphanesiz) ==================
#define HX_DOUT_ANALOG_PIN A7   // A7 sadece analog -> analogRead ile örnekleyeceğiz
#define HX_SCK_PIN         A5   // SCK dijital çıkış olarak kullanılır

// Kalibrasyon parametreleri
// raw_gram = (raw_counts - tare_offset) / scale_factor
// Bu ikisini kendi kalibrasyonuna göre güncelle.
long   tare_offset   = 0;       // ham sayaç ofseti (tare sonrası kaydedilecek)
float  scale_factor  = 936.57f; // sayaç/gram oranı (örnek değer, kalibre et)

// ================== Step motor pinleri ====================
const int stepPin1   = 11;
const int dirPin1    = 10;
const int enablePin1 = A0;

const int stepPin2   = 8;
const int dirPin2    = 9;
const int enablePin2 = A1;

const int stepPin3   = 6;
const int dirPin3    = 4;
const int enablePin3 = A2;

// ================== Diğer çıkışlar ========================
const int airMotorPin   = 5;
const int cameraPin     = 3;
const int waterMotorPin = 7;
const int selenoid      = 2;

// ================== pH Sensör =============================
#define PH_OFFSET   -1.00       // senin istediğin ofset
#define PH_PIN      A3          // pH analog çıkışı
const int PH_LED_PIN = 13;      // pH ölçüm bildirimi için LED

// pH okuma buffer
unsigned long int avgValue;
int buf[10], temp;

// ================== Yardımcılar ===========================
static inline bool hx711IsReady() {
  // HX711 hazır olduğunda DOUT LOW olur.
  // A7’yi analog okuyoruz, ~2.5V eşiği kullanalım.
  int v = analogRead(HX_DOUT_ANALOG_PIN);
  return v < 512; // LOW ~ 0V => <512
}

long readHX711Raw(uint8_t gain_pulses = 1) {
  // Varsayılan: Channel A, gain 128 için 1 ekstra puls.
  // SCK LOW başla
  pinMode(HX_SCK_PIN, OUTPUT);
  digitalWrite(HX_SCK_PIN, LOW);

  // Data hazır olana kadar bekle (DOUT LOW)
  unsigned long t0 = millis();
  while (!hx711IsReady()) {
    if (millis() - t0 > 1000) {
      // 1 sn içinde hazır olmadı -> hata gibi ele al
      return 0;
    }
    delay(1);
  }

  // 24 bit oku (MSB->LSB)
  long value = 0;
  for (int i = 0; i < 24; i++) {
    // SCK HIGH: HX711 bir bit hazırlar
    digitalWrite(HX_SCK_PIN, HIGH);
    delayMicroseconds(2);

    // DOUT seviyesi oku (analogRead ile)
    int a = analogRead(HX_DOUT_ANALOG_PIN);
    uint8_t bit = (a > 512) ? 1 : 0;
    value = (value << 1) | bit;

    // SCK LOW
    digitalWrite(HX_SCK_PIN, LOW);
    delayMicroseconds(2);
  }

  // Gain seçimi için ekstra clock darbeleri (A,128 => 1 pulse)
  for (uint8_t g = 0; g < gain_pulses; g++) {
    digitalWrite(HX_SCK_PIN, HIGH);
    delayMicroseconds(2);
    digitalWrite(HX_SCK_PIN, LOW);
    delayMicroseconds(2);
  }

  // 24-bit iki's tamamından 32-bit signed’a dönüştür
  if (value & 0x800000) {
    value |= ~0xFFFFFFL; // işaret uzat
  }

  return value;
}

float getWeight() {
  // 5 ölçüm ortalaması (gürültüyü azaltır)
  const int N = 5;
  long sum = 0;
  for (int i = 0; i < N; i++) {
    long raw = readHX711Raw(1);  // A kanal, gain128
    sum += raw;
  }
  long raw_avg = sum / N;

  // gram cinsinden
  float weight = (raw_avg - tare_offset) / scale_factor;
  return weight;
}

float getPH() {
  for (int i = 0; i < 10; i++) {
    buf[i] = analogRead(PH_PIN);
    delay(10);
  }

  // Küçükten büyüğe sırala
  for (int i = 0; i < 9; i++) {
    for (int j = i + 1; j < 10; j++) {
      if (buf[i] > buf[j]) {
        temp = buf[i]; buf[i] = buf[j]; buf[j] = temp;
      }
    }
  }

  // Ortadaki 6 değerin ortalaması
  unsigned long sum = 0;
  for (int i = 2; i < 8; i++) sum += buf[i];

  // Voltaja çevir -> pH (sensör örnek formülü)
  float v = (float)sum * 5.0 / 1024.0 / 6.0; // ortalama voltaj
  float phValue = 3.5f * v + PH_OFFSET;

  // Kısa LED bildirimi
  digitalWrite(PH_LED_PIN, HIGH);
  delay(60);
  digitalWrite(PH_LED_PIN, LOW);

  return phValue;
}

// ================== Hareket ===============================
void moveStepper(int pin, int directionPin, int enablePin, long steps) {
  digitalWrite(enablePin, LOW);
  digitalWrite(directionPin, steps >= 0 ? HIGH : LOW);

  unsigned long n = (unsigned long)abs(steps);
  for (unsigned long k = 0; k < n; k++) {
    digitalWrite(pin, HIGH);
    delayMicroseconds(800);
    digitalWrite(pin, LOW);
    delayMicroseconds(800);
  }

  digitalWrite(enablePin, HIGH);
  delay(50);
  Serial.println("DONE");
}

// ================== Komut Yorumlayıcı =====================
void executeCommand(String command) {
  command.trim();

  if (command.startsWith("MOVE1")) {
    long steps = command.substring(6).toInt();
    moveStepper(stepPin1, dirPin1, enablePin1, steps);

  } else if (command.startsWith("MOVE2")) {
    long steps = command.substring(6).toInt();
    moveStepper(stepPin2, dirPin2, enablePin2, steps);

  } else if (command.startsWith("MOVE3")) {
    long steps = command.substring(6).toInt();
    moveStepper(stepPin3, dirPin3, enablePin3, steps);

  } else if (command == "AIR_ON") {
    digitalWrite(airMotorPin, HIGH);  Serial.println("DONE");

  } else if (command == "AIR_OFF") {
    digitalWrite(airMotorPin, LOW);   Serial.println("DONE");

  } else if (command.startsWith("AIR_DUR")) {
    long duration = command.substring(8).toInt();
    digitalWrite(airMotorPin, HIGH); delay(duration);
    digitalWrite(airMotorPin, LOW);  Serial.println("DONE");

  } else if (command == "WATER_ON") {
    digitalWrite(waterMotorPin, HIGH);  Serial.println("DONE");

  } else if (command == "WATER_OFF") {
    digitalWrite(waterMotorPin, LOW);   Serial.println("DONE");

  } else if (command.startsWith("WATER_DUR")) {
    long duration = command.substring(10).toInt();
    digitalWrite(waterMotorPin, HIGH); delay(duration);
    digitalWrite(waterMotorPin, LOW);  Serial.println("DONE");

  } else if (command == "VALVE_ON") {
    digitalWrite(selenoid, HIGH);   Serial.println("DONE");

  } else if (command == "VALVE_OFF") {
    digitalWrite(selenoid, LOW);    Serial.println("DONE");

  } else if (command.startsWith("VALVE_DUR")) {
    long duration = command.substring(10).toInt();
    digitalWrite(selenoid, HIGH); delay(duration);
    digitalWrite(selenoid, LOW);   Serial.println("DONE");

  } else if (command.startsWith("COKME_DUR")) {
    long duration = command.substring(10).toInt();
    delay(duration);               Serial.println("DONE");

  } else if (command == "CAMERA_TRIG") {
    digitalWrite(cameraPin, HIGH); delay(100);
    digitalWrite(cameraPin, LOW);  Serial.println("DONE");

  } else if (command == "WEIGHT_MEASURE") {
    float w = getWeight();
    Serial.print("Weight: ");
    Serial.println(w, 3);

  } else if (command == "PH_MEASURE") {
    float p = getPH();
    Serial.print("pH: ");
    Serial.println(p, 2);
  }
}

// ================== Kurulum & Döngü =======================
void setup() {
  Serial.begin(9600);

  // HX711 pin modları
  pinMode(HX_SCK_PIN, OUTPUT);
  digitalWrite(HX_SCK_PIN, LOW);
  // A7 analog giriş, mod ayarı gerektirmez

  // Step motorlar
  pinMode(stepPin1, OUTPUT); pinMode(dirPin1, OUTPUT); pinMode(enablePin1, OUTPUT);
  pinMode(stepPin2, OUTPUT); pinMode(dirPin2, OUTPUT); pinMode(enablePin2, OUTPUT);
  pinMode(stepPin3, OUTPUT); pinMode(dirPin3, OUTPUT); pinMode(enablePin3, OUTPUT);

  // Diğer pinler
  pinMode(airMotorPin, OUTPUT);
  pinMode(waterMotorPin, OUTPUT);
  pinMode(selenoid, OUTPUT);
  pinMode(cameraPin, OUTPUT);

  // Başlangıç
  digitalWrite(enablePin1, HIGH);
  digitalWrite(enablePin2, HIGH);
  digitalWrite(enablePin3, HIGH);
  digitalWrite(selenoid, LOW);
  digitalWrite(cameraPin, LOW);

  // pH LED
  pinMode(PH_LED_PIN, OUTPUT);
  digitalWrite(PH_LED_PIN, LOW);

  // İlk tara (opsiyonel): kaba tare almak istersen burada bir kez oku
  long raw0 = readHX711Raw(1);
  tare_offset = raw0;   // boşken çağırdıysan ham değeri ofset yap
  Serial.println("Sistem hazir.");
}

void loop() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    executeCommand(command);
  }
}

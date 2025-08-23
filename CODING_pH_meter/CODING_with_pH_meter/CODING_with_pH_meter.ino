#include "HX711.h"
#include <Arduino.h>

// HX711 pinleri
#define DT_PIN A7
#define SCK_PIN A5

HX711 scale;

// Kaydedilmiş tare offset değeri
long tare_offset = 936.99;
float manual_offset_correction = 0; // Manuel eklemek için düzeltme değeri

// Step motor pinleri
const int stepPin1 = 11;
const int dirPin1 = 10;
const int enablePin1 = A0;

const int stepPin2 = 8;
const int dirPin2 = 9;
const int enablePin2 = A1;

const int stepPin3 = 6;
const int dirPin3 = 4;
const int enablePin3 = A2;

// Diğer pinler
const int airMotorPin = 5;
const int cameraPin = 3;
const int waterMotorPin = 7;
const int selenoid = 2;

// ----------- pH Sensör Ayarları ------------
#define PH_OFFSET -1.00         // Eğer offset varsa düzeltme
#define SensorPin A3            // pH sensör analog çıkışı (boşta olan bir analog pin seç)
unsigned long int avgValue;     
float b;
int buf[10], temp;
// ------------------------------------------

bool testRunning = false;
int transferCount = 0;
int i = 0;

void setup() {
  Serial.begin(9600);

  // HX711 başlat
  scale.begin(DT_PIN, SCK_PIN);
  scale.tare();  
  scale.set_offset(tare_offset);
  Serial.println("HX711 offset yüklendi.");

  scale.set_scale(936.57);  // Kalibrasyon değeri

  // Step motor pinleri
  pinMode(stepPin1, OUTPUT);
  pinMode(dirPin1, OUTPUT);
  pinMode(enablePin1, OUTPUT);

  pinMode(stepPin2, OUTPUT);
  pinMode(dirPin2, OUTPUT);
  pinMode(enablePin2, OUTPUT);

  pinMode(stepPin3, OUTPUT);
  pinMode(dirPin3, OUTPUT);
  pinMode(enablePin3, OUTPUT);

  // Diğer pinler
  pinMode(airMotorPin, OUTPUT);
  pinMode(waterMotorPin, OUTPUT);
  pinMode(selenoid, OUTPUT);
  pinMode(cameraPin, OUTPUT);

  // Motorları devre dışı bırak
  digitalWrite(enablePin1, HIGH);
  digitalWrite(enablePin2, HIGH);
  digitalWrite(enablePin3, HIGH);
  digitalWrite(selenoid, LOW);
  digitalWrite(cameraPin, LOW);

  pinMode(13, OUTPUT);  // pH test LED
}

// ----------------- FONKSİYONLAR ------------------

// Ağırlık ölçüm
float getWeight() {
  if (!scale.is_ready()) {
    Serial.println("HX711 hazır değil, ağırlık ölçümü yapılamadı.");
    return 0.0;
  }
  float weight = scale.get_units(5);
  weight += manual_offset_correction;
  return weight;
}

// pH ölçüm
float getPH() {
  for (int i = 0; i < 10; i++) {
    buf[i] = analogRead(SensorPin);
    delay(10);
  }
  for (int i = 0; i < 9; i++) {
    for (int j = i + 1; j < 10; j++) {
      if (buf[i] > buf[j]) {
        temp = buf[i];
        buf[i] = buf[j];
        buf[j] = temp;
      }
    }
  }
  avgValue = 0;
  for (int i = 2; i < 8; i++) {
    avgValue += buf[i];
  }

  float phValue = (float)avgValue * 5.0 / 1024 / 6; 
  phValue = 3.5 * phValue;   
  phValue = phValue + PH_OFFSET;

  digitalWrite(13, HIGH);  
  delay(100);
  digitalWrite(13, LOW);

  return phValue;
}

// Komutları çalıştır
void executeCommand(String command) {
  if (command.startsWith("MOVE1")) {
    long steps = command.substring(6).toInt();
    moveStepper(stepPin1, dirPin1, enablePin1, steps);
    Serial.println("DONE");
  } else if (command.startsWith("MOVE2")) {
    long steps = command.substring(6).toInt();
    moveStepper(stepPin2, dirPin2, enablePin2, steps);
    Serial.println("DONE");
  } else if (command.startsWith("MOVE3")) {
    long steps = command.substring(6).toInt();
    moveStepper(stepPin3, dirPin3, enablePin3, steps);
    Serial.println("DONE");
  } else if (command.startsWith("AIR_ON")) {
    digitalWrite(airMotorPin, HIGH);
    Serial.println("DONE");
  } else if (command.startsWith("AIR_OFF")) {
    digitalWrite(airMotorPin, LOW);
    Serial.println("DONE");
  } else if (command.startsWith("AIR_DUR")) {
    long duration = command.substring(8).toInt();
    digitalWrite(airMotorPin, HIGH);
    delay(duration);
    digitalWrite(airMotorPin, LOW);
    Serial.println("DONE");
  } else if (command.startsWith("WATER_ON")) {
    digitalWrite(waterMotorPin, HIGH);
    Serial.println("DONE");
  } else if (command.startsWith("WATER_OFF")) {
    digitalWrite(waterMotorPin, LOW);
    Serial.println("DONE");
  } else if (command.startsWith("WATER_DUR")) {
    long duration = command.substring(10).toInt();
    digitalWrite(waterMotorPin, HIGH);
    delay(duration);
    digitalWrite(waterMotorPin, LOW);
    Serial.println("DONE");
  } else if (command.startsWith("VALVE_ON")) {
    digitalWrite(selenoid, HIGH);
    Serial.println("DONE");
  } else if (command.startsWith("VALVE_OFF")) {
    digitalWrite(selenoid, LOW);
    Serial.println("DONE");
  } else if (command.startsWith("VALVE_DUR")) {
    long duration = command.substring(10).toInt();
    digitalWrite(selenoid, HIGH);
    delay(duration);
    digitalWrite(selenoid, LOW);
    Serial.println("DONE");
  } else if (command.startsWith("COKME_DUR")) {
    long duration = command.substring(10).toInt();
    delay(duration);
    Serial.println("DONE");
  } else if (command.startsWith("CAMERA_TRIG")) {
    digitalWrite(cameraPin, HIGH);
    delay(100);
    digitalWrite(cameraPin, LOW);
    Serial.println("DONE");
  } else if (command.startsWith("WEIGHT_MEASURE")) {
    delay(100);
    float weight = getWeight();
    Serial.print("Weight: ");
    Serial.println(weight);
  } else if (command.startsWith("PH_MEASURE")) {
    float phVal = getPH();
    Serial.print("pH: ");
    Serial.println(phVal, 2);
  }
}

// Step motor fonksiyonu
void moveStepper(int pin, int directionPin, int enablePin, long steps) {
  digitalWrite(enablePin, LOW);
  digitalWrite(directionPin, steps >= 0 ? HIGH : LOW);
  for (i = 0; i < abs(steps); i++) {
    digitalWrite(pin, HIGH);
    delayMicroseconds(800);
    digitalWrite(pin, LOW);
    delayMicroseconds(800);
  }
  digitalWrite(enablePin, HIGH);
  delay(100);
  Serial.println("DONE");
}

// --------------------------------------------------

void loop() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    executeCommand(command);
  }
}

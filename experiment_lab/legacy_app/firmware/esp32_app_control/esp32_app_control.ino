#include <Arduino.h>
#include <Wire.h>
#include <ESP32Servo.h>
#include <Adafruit_BNO055.h>
#include <utility/imumaths.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

// App protocol v2. Original esp32_new_ver sketch is preserved separately.
// Targets are desired IMU angles relative to saved level, NOT servo degrees.
// Missing app targets for 500 ms falls back to local IMU leveling.
float targetRollDeg = 0, targetPitchDeg = 0;
unsigned long lastAppTarget = 0;
bool appTimedOut = false;
char appLine[80];
size_t appLineLength = 0;
bool appReceiving = false, appOverflow = false;
unsigned long appFrameStarted = 0;

// =====================================
// 1. ESP32-S3 핀 설정 (보드: ESP32S3 Dev Module)
// =====================================
const int ROLL_PIN  = 18;
const int PITCH_PIN = 17;   // S3: 19번은 USB-OTG용이라 17번으로 변경

// BNO055 통신 핀 (모듈의 ATX, LRX 핀과 연결)
const int SDA_PIN = 8;      // S3: 21 → 8
const int SCL_PIN = 9;      // S3: 22번 없음 → 9

// =====================================
// 2. 서보 설정 (제한 해제)
// =====================================
const int CENTER = 1500;
const int RANGE = 400;

const int STEP = 80;
const int FINE_STEP = 10;

const int MIN_PWM = CENTER - RANGE;
const int MAX_PWM = CENTER + RANGE;

// =====================================
// 3. 자동 왕복 설정
// =====================================
const float PERIODS[] = { 4.0, 2.0, 1.0, 0.5, 0.3 };
const int N_PERIOD = 5;
int periodIdx = 3;

int amp = 60;
const int AMP_STEP = 20;
const unsigned long UPDATE_MS = 10;

// =====================================
// 4. 수평 유지 제어 설정 (속도 및 범위 개선)
// =====================================
const float KP = 10.0f;
const int MAX_COMP = 400;
const unsigned long LEVEL_MS = 10;   // [수정 2] 15 → 10 ms (100 Hz)

// [수정 2] 서보 신호 주기: 50 Hz(20 ms) → 100 Hz(10 ms)
//   디지털 서보는 보통 100~333 Hz 지원. 서보가 뜨거워지거나 이상하게 떨면 50으로 되돌릴 것
const int SERVO_HZ = 100;
// [수정 2] 서보 명령을 실수로 계산해 부드럽게: 저역통과 + 속도 제한
const float OUT_LPF_HZ = 5.0f;       // 명령 저역통과 [Hz]
const float MAX_RATE = 1000.0f;      // 최대 명령 속도 [us/s] (기존 20us/15ms ≈ 1333 us/s)
float rollCmd = 1500, pitchCmd = 1500;
const float DEADBAND = 0.5f;
const float FILTER_ALPHA = 0.4f; 
const unsigned long IMU_TIMEOUT = 300; 

// =====================================
// 5. 모터 객체 및 상태
// =====================================
Servo rollServo;
Servo pitchServo;

int rollPWM = CENTER;
int pitchPWM = CENTER;

int rollHome = CENTER;
int pitchHome = CENTER;

bool rollReady = false;
bool pitchReady = false;

int autoMode = 0;

float phase = 0;
unsigned long lastUpdate = 0;
unsigned long lastLevelUpdate = 0;

// =====================================
// 6. BNO055 센서 설정 및 워치독(기절 방지) 변수
// =====================================
Adafruit_BNO055 bno = Adafruit_BNO055(55, 0x29); // 안되면 0x28로 변경

bool imuFound = false;
bool imuValid = false;

float rawRoll = 0;
float rawPitch = 0;
float smoothRoll = 0;
float smoothPitch = 0;
float zeroRoll = 0;
float zeroPitch = 0;

bool zeroSaved = false;
bool directionChecked = false;

unsigned long lastIMU = 0;

int rollDir = 1;
int pitchDir = 1;

// 센서 다운 감지용 변수
float prevRawRoll = -999.0;
float prevRawPitch = -999.0;
unsigned long lastDataChangeTime = 0;
const unsigned long FROZEN_TIMEOUT = 2000;   // 잘못된 값이 2초 이상 이어지면 재초기화
const unsigned long IMU_WARMUP = 1500;       // 초기화 후 퓨전 시작 대기 [ms]
unsigned long imuInitTime = 0;

// =====================================
// 8. BNO055 초기화
// =====================================
void initializeIMU() {
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000); 

  Serial.println("Connecting BNO055...");

  if (!bno.begin()) {
    Serial.println("BNO055 NOT FOUND - Check ATX/LRX Wiring!");
    imuFound = false;
    return;
  }

  delay(50);
  bno.setExtCrystalUse(true);
  imuFound = true;
  imuInitTime = millis();

  Serial.println("BNO055 CONNECTED & READY");
}

// =====================================
// 9. BNO055 Roll / Pitch 읽기 (축 변경 적용)
// =====================================
void readIMU() {
  if (!imuFound) return;

  imu::Quaternion quat = bno.getQuat();

  float qw = quat.w();
  float qx = quat.x();
  float qy = quat.y();
  float qz = quat.z();

  // 기존 계산식 유지
  float calcRollRad = atan2f(2.0f * (qw*qx + qy*qz), 1.0f - 2.0f * (qx*qx + qy*qy));
  float value = 2.0f * (qw*qy - qz*qx);
  value = constrain(value, -1.0f, 1.0f);
  float calcPitchRad = asinf(value);

  // ★ Roll과 Pitch를 서로 맞바꾸어 축 방향 오류 해결 ★
  rawRoll = calcPitchRad * 180.0f / PI;
  rawPitch = calcRollRad * 180.0f / PI;

  if (!isfinite(rawRoll) || !isfinite(rawPitch)) return;

  // 센서 기절 감지 (S3 수정)
  //  - 가만히 놓여 있으면 쿼터니언이 똑같이 유지될 수 있으므로 '값이 안 변함'만으로는 판단하지 않음
  //  - 쿼터니언 크기가 1이 아니면(0,0,0,0 = 통신 실패/퓨전 미시작) 비정상으로 봄
  //  - 재초기화 직후 IMU_WARMUP 동안은 검사하지 않음 (퓨전 시작 대기)
  float qnorm = sqrtf(qw*qw + qx*qx + qy*qy + qz*qz);
  bool quatBad = fabsf(qnorm - 1.0f) > 0.1f;
  if (millis() - imuInitTime < IMU_WARMUP) {
    lastDataChangeTime = millis();
    if (quatBad) return;            // 워밍업 중 잘못된 값은 버림
  } else if (quatBad) {
    if (millis() - lastDataChangeTime > FROZEN_TIMEOUT) {
      Serial.printf("!!! IMU BAD DATA (|q|=%.3f) !!! AUTO REBOOTING IMU...\n", qnorm);
      initializeIMU();
      lastDataChangeTime = millis();
    }
    return;                          // 잘못된 값은 제어에 쓰지 않음
  } else {
    lastDataChangeTime = millis();
  }
  prevRawRoll = rawRoll;
  prevRawPitch = rawPitch;

  if (!imuValid) {
    smoothRoll = rawRoll;
    smoothPitch = rawPitch;
  } else {
    smoothRoll += FILTER_ALPHA * (rawRoll - smoothRoll);
    smoothPitch += FILTER_ALPHA * (rawPitch - smoothPitch);
  }

  imuValid = true;
  lastIMU = millis();
}

// =====================================
// 10. 센서 정상 여부 확인
// =====================================
bool imuFresh() {
  return imuFound && imuValid && (millis() - lastIMU < IMU_TIMEOUT);
}

// =====================================
// 11. 현재 상태 표시
// =====================================
void showStatus() {
  Serial.println("-------------------------");
  Serial.printf("ROLL=%d (%s)\n", rollPWM, rollReady ? "ON" : "OFF");
  Serial.printf("PITCH=%d (%s)\n", pitchPWM, pitchReady ? "ON" : "OFF");
  Serial.printf("MODE=%d\n", autoMode);
  Serial.println("PROTO=2");
  Serial.printf("TARGET R=%.3f P=%.3f\n", targetRollDeg, targetPitchDeg);
  Serial.printf("APP_TIMEOUT=%d\n", appTimedOut ? 1 : 0);

  if (imuFresh()) {
    Serial.printf("IMU R=%.2f deg | P=%.2f deg\n", smoothRoll - zeroRoll, smoothPitch - zeroPitch);
  } else {
    Serial.println("IMU DATA NOT READY");
  }

  Serial.printf("HOME R=%d P=%d\n", rollHome, pitchHome);
  Serial.printf("ZERO=%d DIR=%d\n", zeroSaved, directionChecked);
  Serial.println("-------------------------");
}

// =====================================
// 12. 모터 초기화 및 구동 함수들
// =====================================
void enableRoll() {
  if (rollReady) return;
  rollServo.setPeriodHertz(SERVO_HZ);
  rollServo.attach(ROLL_PIN, 500, 2500);
  rollPWM = CENTER;
  rollServo.writeMicroseconds(rollPWM);
  rollReady = true;
  zeroSaved = false; directionChecked = false;
  Serial.println("ROLL ENABLED");
}

void enablePitch() {
  if (pitchReady) return;
  pitchServo.setPeriodHertz(SERVO_HZ);
  pitchServo.attach(PITCH_PIN, 500, 2500);
  pitchPWM = CENTER;
  pitchServo.writeMicroseconds(pitchPWM);
  pitchReady = true;
  zeroSaved = false; directionChecked = false;
  Serial.println("PITCH ENABLED");
}

void moveRoll(int delta) {
  autoMode = 0;
  targetRollDeg = targetPitchDeg = 0;
  zeroSaved = false; directionChecked = false;
  if (!rollReady) return;
  rollPWM = constrain(rollPWM + delta, MIN_PWM, MAX_PWM);
  rollServo.writeMicroseconds(rollPWM);
}

void movePitch(int delta) {
  autoMode = 0;
  targetRollDeg = targetPitchDeg = 0;
  zeroSaved = false; directionChecked = false;
  if (!pitchReady) return;
  pitchPWM = constrain(pitchPWM + delta, MIN_PWM, MAX_PWM);
  pitchServo.writeMicroseconds(pitchPWM);
}

void startAuto(int mode) {
  if (((mode == 1 || mode == 3) && !rollReady) || ((mode == 2 || mode == 3) && !pitchReady)) return;
  autoMode = mode;
  phase = 0;
  lastUpdate = millis();
}

void saveLevelZero() {
  autoMode = 0;
  if (!rollReady || !pitchReady || !imuFresh()) return;
  zeroRoll = smoothRoll;
  zeroPitch = smoothPitch;
  rollHome = rollPWM;
  pitchHome = pitchPWM;
  zeroSaved = true;
  directionChecked = false;
  Serial.println("=== LEVEL ZERO SAVED ===");
}

void startLevelControl() {
  if (!rollReady || !pitchReady || !imuFresh() || !zeroSaved || !directionChecked) return;
  rollCmd = rollPWM;      // [수정 2] 현재 위치에서 이어받기
  pitchCmd = pitchPWM;
  autoMode = 4;
  targetRollDeg = targetPitchDeg = 0;
  appTimedOut = false;
  lastLevelUpdate = millis();
  Serial.println("=== IMU LEVEL CONTROL ON (NO LIMITS) ===");
}

// =====================================
// 19. 자동 수평 유지 P 제어
// =====================================
void updateLevelControl(float dt) {
  if ((autoMode != 4 && autoMode != 5) || !imuFresh()) return;

  float rollError = smoothRoll - zeroRoll - targetRollDeg;
  float pitchError = smoothPitch - zeroPitch - targetPitchDeg;

  // [수정 1] 소프트 데드밴드: 경계에서 0부터 연속으로 커짐 (출력 계단 없음)
  if (rollError > DEADBAND) rollError -= DEADBAND;
  else if (rollError < -DEADBAND) rollError += DEADBAND;
  else rollError = 0;
  if (pitchError > DEADBAND) pitchError -= DEADBAND;
  else if (pitchError < -DEADBAND) pitchError += DEADBAND;
  else pitchError = 0;

  float targetRoll = rollHome - rollDir * KP * rollError;
  float targetPitch = pitchHome - pitchDir * KP * pitchError;

  targetRoll = constrain(targetRoll, (float)max(MIN_PWM, rollHome - MAX_COMP), (float)min(MAX_PWM, rollHome + MAX_COMP));
  targetPitch = constrain(targetPitch, (float)max(MIN_PWM, pitchHome - MAX_COMP), (float)min(MAX_PWM, pitchHome + MAX_COMP));

  // [수정 2] 저역통과 + 속도 제한을 실수로 계산 → 작은 단위로 매끄럽게 이동
  float a = 1.0f - expf(-2.0f * PI * OUT_LPF_HZ * dt);
  float maxStep = MAX_RATE * dt;
  rollCmd  += constrain(a * (targetRoll  - rollCmd),  -maxStep, maxStep);
  pitchCmd += constrain(a * (targetPitch - pitchCmd), -maxStep, maxStep);

  rollPWM = (int)lroundf(rollCmd);
  pitchPWM = (int)lroundf(pitchCmd);
  rollServo.writeMicroseconds(rollPWM);
  pitchServo.writeMicroseconds(pitchPWM);
}

void stopMotors() {
  autoMode = 0;
  targetRollDeg = targetPitchDeg = 0;
  if (rollReady) rollServo.detach();
  if (pitchReady) pitchServo.detach();
  rollReady = false;
  pitchReady = false;
  directionChecked = false;
  Serial.println("PWM OUTPUT DETACHED");
}

// '@' frames are consumed atomically. Payload bytes never reach legacy keys,
// even after an overflow or malformed packet. A closing newline is mandatory.
void applyAppLine() {
  float roll, pitch; int consumed = 0;
  if (appOverflow || sscanf(appLine, "T,%f,%f%n", &roll, &pitch, &consumed) != 2 ||
      appLine[consumed] != '\0' || !isfinite(roll) || !isfinite(pitch) ||
      fabsf(roll) > 8.0f || fabsf(pitch) > 8.0f) return;
  if (!rollReady || !pitchReady || !imuFresh() || !zeroSaved || !directionChecked) return;
  if (autoMode != 5) {
    rollCmd = rollPWM; pitchCmd = pitchPWM; lastLevelUpdate = millis();
  }
  targetRollDeg = roll; targetPitchDeg = pitch;
  lastAppTarget = millis(); appTimedOut = false; autoMode = 5;
}

// =====================================
// 21. SETUP
// =====================================
void setup() {
  Serial.begin(115200);
  delay(300);

  Serial.println("\n==============================");
  Serial.println("HONGIK APP CONTROL PROTOCOL 2 + BNO055");
  Serial.println("AXIS SWAPPED TRACKING");
  Serial.println("==============================");

  initializeIMU();
}

// =====================================
// 22. LOOP
// =====================================
void loop() {
  readIMU();
  if (appReceiving && millis()-appFrameStarted > 100) appOverflow = true;

  while (Serial.available()) {
    char cmd = Serial.read();
    if (appReceiving) {
      if (cmd == '\n') {
        appLine[appLineLength] = '\0'; applyAppLine();
        appReceiving = false; appLineLength = 0; appOverflow = false;
      } else if (cmd != '\r') {
        if (appLineLength < sizeof(appLine)-1) appLine[appLineLength++] = cmd;
        else appOverflow = true;
      }
      continue;
    }
    if (cmd == '@') { appReceiving = true; appLineLength = 0; appOverflow = false; appFrameStarted = millis(); continue; }
    switch (cmd) {
      case '1': enableRoll(); break;
      case '2': enablePitch(); break;
      case 'q': moveRoll(-STEP); break;
      case 'w': moveRoll(STEP); break;
      case 'e': movePitch(-STEP); break;
      case 'r': movePitch(STEP); break;
      case 'Q': moveRoll(-FINE_STEP); break;
      case 'W': moveRoll(FINE_STEP); break;
      case 'E': movePitch(-FINE_STEP); break;
      case 'R': movePitch(FINE_STEP); break;
      case 'z':
        autoMode = 0;
        rollPWM = CENTER;
        pitchPWM = CENTER;
        if (rollReady) rollServo.writeMicroseconds(rollPWM);
        if (pitchReady) pitchServo.writeMicroseconds(pitchPWM);
        break;
      case 'a': startAuto(1); break;
      case 'b': startAuto(2); break;
      case 'c': startAuto(3); break;
      case 'n': if (periodIdx > 0) periodIdx--; break;
      case 'm': if (periodIdx < N_PERIOD - 1) periodIdx++; break;
      case 'j': amp = max(0, amp - AMP_STEP); break;
      case 'k': amp = min(RANGE, amp + AMP_STEP); break;
      case 'g': saveLevelZero(); break;
      case 'u': autoMode = 0; rollDir *= -1; directionChecked = false; break;
      case 'i': autoMode = 0; pitchDir *= -1; directionChecked = false; break;
      case 'v': 
        if (zeroSaved && imuFresh()) {
          directionChecked = true;
          Serial.println("DIRECTIONS CONFIRMED");
        }
        break;
      case 'h': startLevelControl(); break;
      case 'x': autoMode = 0; targetRollDeg = targetPitchDeg = 0; Serial.println("AUTO STOPPED - PWM HELD"); break;
      case 's': stopMotors(); break;
      case 'p': showStatus(); break;
    }
  }

  unsigned long now = millis();
  if (autoMode == 5 && now - lastAppTarget > 500) {
    targetRollDeg = targetPitchDeg = 0; autoMode = 4; appTimedOut = true;
    Serial.println("APP TIMEOUT - LOCAL LEVEL FALLBACK");
    showStatus();
  }
  if ((autoMode == 4 || autoMode == 5) && !imuFresh()) {
    // Hold the last bounded PWM; never control from stale IMU data.
    targetRollDeg = targetPitchDeg = 0; autoMode = 0;
    Serial.println("IMU TIMEOUT - PWM HELD");
    showStatus();
  }

  if (autoMode >= 1 && autoMode <= 3 && now - lastUpdate >= UPDATE_MS) {
    float dt = (now - lastUpdate) / 1000.0f;
    lastUpdate = now;
    phase += 2.0f * PI * dt / PERIODS[periodIdx];
    if (phase >= 2.0f * PI) phase = fmodf(phase, 2.0f * PI);
    
    int offset = (int)lroundf(amp * sinf(phase));
    int pwm = constrain(CENTER + offset, MIN_PWM, MAX_PWM);

    if (autoMode == 1 || autoMode == 3) {
      rollPWM = pwm;
      rollServo.writeMicroseconds(rollPWM);
    }
    if (autoMode == 2 || autoMode == 3) {
      pitchPWM = pwm;
      pitchServo.writeMicroseconds(pitchPWM);
    }
  }

  if ((autoMode == 4 || autoMode == 5) && now - lastLevelUpdate >= LEVEL_MS) {
    float dt = (now - lastLevelUpdate) / 1000.0f;
    lastLevelUpdate = now;
    updateLevelControl(dt);
  }

  static unsigned long lastPrint = 0;
  if ((autoMode == 4 || autoMode == 5) && now - lastPrint >= 500) {
    lastPrint = now;
    showStatus();
  }
}

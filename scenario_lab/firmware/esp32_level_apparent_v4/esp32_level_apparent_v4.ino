#include <Arduino.h>
#include <Wire.h>
#include <ESP32Servo.h>
#include <Adafruit_BNO055.h>
#include <utility/imumaths.h>
#include <Preferences.h>
#include <math.h>

// =====================================================================
// 절대 수평(중력 기준 0°) 유지 버전  +  [추가] 겉보기 중력 정렬 모드 (급정지·급가속 대응)
//  [추가] 'H' : 겉보기 중력 모드 ON (트레이 가속도계 e = atan2(f_x, f_z) → 0)
//  [추가] 'h' : 기존 수평 유지 모드 (그대로)
//  [추가] 'l' : CSV 로그 ON/OFF (50 Hz)   'A' : 퓨전각 vs 가속도각 비교 출력(축·부호 확인)
//  [추가] '[' / ']' : 겉보기 중력 모드 게인 ×0.8 / ×1.25 (실험 중 튜닝)
//  ※ 'g' 영점을 이 버전에서 한 번 다시 저장해야 함 (가속도 영점이 추가됨)
//  - 수평계로 맞춘 상태에서 'g' → IMU 장착 오차를 플래시에 저장 (재부팅해도 유지)
//  - P + I 제어: 경사로처럼 계속 기울어진 상황에서도 오차를 0까지 보정
//  - 'G' : 저장된 영점 삭제,  'p' : 상태 + BNO055 보정 상태 출력
//
// [v2 추가] 'J' : 겉보기 중력 모드 - 빠른 버전 (모드 6)
//   모드 5는 가속도각 e 전체를 6 Hz LPF에 통과시켜서, 트레이가 '자기가 움직인 결과'도 늦게 봄.
//   모드 6은 두 성분을 나눔:
//     app  = 퓨전각 − 가속도각         → 세계 기준 겉보기 중력 방향 (정지 시 0, 급정거 시 atan(a/g))
//     appF = LPF(app, APP_LPF_HZ)      → 필터는 외란(로봇 가속도)에만
//     e    = 퓨전각 − appF             → 트레이 자기 움직임은 자이로 기반 퓨전각으로 지연 없이 들어감
//   정상상태 결과는 모드 5와 같고, 루프 안 지연이 줄어 게인을 더 올릴 수 있음.
//   덤: a_est = g·tan(appF) → 차체 IMU 없이 로봇 가속도 추정 (CSV 로그에 기록)
//   튜닝 키 (모드 6 전용): '(' ')' KP_J ×0.8/×1.25,  '{' '}' KI_J ×0.8/×1.25,
//                          '<' '>' 출력 LPF ×0.8/×1.25,  'O' 출력 LPF 켜기/끄기
//   ※ 주의: BNO055 퓨전각이 긴 가속 중 겉보기 중력 쪽으로 끌려가면 이 방식은 못 씀
//           → 사용 전 '퓨전각 끌림 확인' 절차(파일 끝 주석)로 먼저 확인
//
// [v3 추가] 축별 ON/OFF + 데드밴드 조절 (재업로드 없이 실험)
//   'P' : 피치 자동제어 ON/OFF  (OFF = 피치는 영점 위치(home)에 고정)
//   'K' : 롤   자동제어 ON/OFF  (OFF = 롤은 영점 위치(home)에 고정)
//   ',' / '.' : 겉보기 중력 모드(H, J) 데드밴드 −0.1° / +0.1°  (기본 0.3°, 0.1~3.0°)
//               계속 미세하게 움직이면 '.'로 키워 봄
//   모든 자동 모드(h, H, J)에 적용. 'p'에 현재 상태 표시
//
// [v4 추가] 출발·정지를 부드럽게 (모든 자동 모드 h, H, J)
//   v3 출력단 = 1차 LPF + 속도 제한뿐 → 속도는 제한돼도 '가속도'는 제한이 없어서
//   움직이기 시작하는 순간과 멈추는 순간에 서보가 툭 출발·툭 정지 → 트레이 회전 가속도 × (회전축~음료 높이)
//   만큼 음료를 옆으로 밀어서 출렁임을 만듦.
//   v4 출력단:
//     ① 속도·가속도 제한 추종기 (사다리꼴 속도 프로파일): 가속도 MAX_ACC로 서서히 출발,
//        목표 앞에서 미리 감속해서 멈춤. 작은 오차에서는 v3의 1차 LPF와 똑같이 동작
//     ② S커브 평활 LPF (SMOOTH_HZ): 가속도가 계단처럼 바뀌는 것까지 둥글게 만듦
//     ③ 안티와인드업: 출력이 제한에 걸려 목표를 못 따라가는 동안 적분이 쌓이지 않게 함 (오버슈트 방지)
//   'S'       : 부드러운 출력 ON/OFF (OFF = v3와 동일한 출력) → 같은 조건에서 A/B 비교용
//   '-' / '=' : MAX_ACC  ×0.8 / ×1.25  (낮출수록 부드럽지만 반응이 늦어짐)
//   '_' / '+' : MAX_RATE ×0.8 / ×1.25
//   't' / 'T' : SMOOTH_HZ ×0.8 / ×1.25 (낮출수록 더 둥글지만 지연 증가)
//   ※ 부드럽게 할수록 트레이가 늦게 기울어 그 사이 겉보기 중력 오차가 생김 → 둘 사이 절충점을 로그로 찾기
// =====================================================================

// 1. ESP32-S3 핀
const int ROLL_PIN  = 18;
const int PITCH_PIN = 17;
const int SDA_PIN = 8;
const int SCL_PIN = 9;

// 2. 서보
const int CENTER = 1500;
const int RANGE = 400;
const int STEP = 80;
const int FINE_STEP = 10;
const int MIN_PWM = CENTER - RANGE;
const int MAX_PWM = CENTER + RANGE;
const int SERVO_HZ = 100;   // 서보가 뜨거워지거나 떨면 50으로

// 3. 자동 왕복 (테스트용)
const float PERIODS[] = { 4.0, 2.0, 1.0, 0.5, 0.3 };
const int N_PERIOD = 5;
int periodIdx = 3;
int amp = 60;
const int AMP_STEP = 20;
const unsigned long UPDATE_MS = 10;

// 4. 수평 유지 제어
const float KP = 10.0f;            // [us/deg]
const float KI = 15.0f;            // [us/(deg·s)] 10~30 사이 조절. 천천히 출렁이면 ↓, 수렴 느리면 ↑
const float DEADBAND = 0.3f;       // [deg] 이 안에서는 적분 정지(백래시 헌팅 방지)
const int MAX_COMP = 400;
const unsigned long LEVEL_MS = 10;
const float OUT_LPF_HZ = 5.0f;
float MAX_RATE = 1000.0f;          // [us/s]  [v4] '_' '+'로 조절 가능하게 변수로 바꿈
// [v4] 부드러운 출발·정지
bool  smoothMotion = true;         // 'S'
float MAX_ACC = 3000.0f;           // [us/s^2] 1000 us/s까지 0.33 s에 걸쳐 가속. 출렁이면 '-', 너무 늦으면 '='
float SMOOTH_HZ = 8.0f;            // [Hz] S커브 평활. 출발 순간 '톡' 느낌이 남으면 't'로 낮춤
const float AW_GAP = 20.0f;        // [us] 목표와 출력 차이가 이보다 크면 그 방향 적분 정지
const float FILTER_ALPHA = 0.4f;

// [추가] 겉보기 중력 모드
const float ACC_LPF_HZ = 6.0f;     // 가속도 노이즈 필터. 낮출수록 조용하지만 느려짐(6 Hz ≈ 27 ms 지연)
float KP_A = 5.0f;                 // [us/deg] 비례는 작게 (위치 서보 + 지연 → 크면 떨림)
float KI_A = 60.0f;                // [us/(deg·s)] 반응 속도를 정하는 주 게인. 떨리면 '[' , 느리면 ']'
float DEADBAND_A = 0.3f;           // [v3] ',' '.'로 조절 가능하게 변수로 바꿈
bool pitchCtrlOn = true;           // [v3] 'P'
bool rollCtrlOn  = true;           // [v3] 'K'
const unsigned long IMU_TIMEOUT = 300;

// [v2 추가] 겉보기 중력 모드 - 빠른 버전 (모드 6)
const float APP_LPF_HZ = 6.0f;     // 외란(겉보기 중력 방향)에만 거는 필터. 모드 5의 ACC_LPF_HZ와 같은 값에서 시작
float KP_J = 5.0f;                 // [us/deg] 처음엔 모드 5와 같은 값 → 비교 후 '('/')'로 올려 봄
float KI_J = 60.0f;                // [us/(deg·s)] 비례를 올리면 '{'로 낮춤
float OUT_LPF_J = 10.0f;           // [Hz] 출력 LPF (모드 4·5는 5 Hz 고정)
bool  outLpfJOn = true;
const float G_ACC = 9.81f;

float rollCmd = 1500, pitchCmd = 1500;
float rollInteg = 0, pitchInteg = 0;
// [v4] 추종기 상태
float rollProf = 1500, pitchProf = 1500;     // ① 사다리꼴 프로파일 출력 [us]
float rollVel = 0, pitchVel = 0;             // ① 현재 속도 [us/s]
float lastTargetRoll = 1500, lastTargetPitch = 1500;

// 5. 모터 상태
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

// 6. BNO055
Adafruit_BNO055 bno = Adafruit_BNO055(55, 0x29); // 안되면 0x28
bool imuFound = false;
bool imuValid = false;
float rawRoll = 0, rawPitch = 0;
float smoothRoll = 0, smoothPitch = 0;
float zeroRoll = 0, zeroPitch = 0;   // = IMU 장착 오차 (트레이가 진짜 수평일 때 IMU가 읽는 값)
// [추가] 가속도계 기반 각 (= 트레이 법선과 겉보기 중력 사이의 각)
float accRoll = 0, accPitch = 0;      // 필터 후 [deg]
float zeroAccRoll = 0, zeroAccPitch = 0;
bool accZeroSaved = false;
bool accInit = false;
unsigned long lastAccMicros = 0;
// [v2 추가] 세계 기준 겉보기 중력 방향 (필터 후) [deg]
float appRollF = 0, appPitchF = 0;
bool logging = false;
bool zeroSaved = false;
bool directionChecked = false;
unsigned long lastIMU = 0;
int rollDir = 1;
int pitchDir = 1;
unsigned long lastDataChangeTime = 0;
const unsigned long FROZEN_TIMEOUT = 2000;
const unsigned long IMU_WARMUP = 1500;
unsigned long imuInitTime = 0;

// 7. 플래시 저장
Preferences prefs;

void loadCalibration() {
  prefs.begin("level", true);
  zeroSaved = prefs.getBool("saved", false);
  if (zeroSaved) {
    zeroRoll  = prefs.getFloat("zR", 0);
    zeroPitch = prefs.getFloat("zP", 0);
    rollHome  = prefs.getInt("hR", CENTER);
    pitchHome = prefs.getInt("hP", CENTER);
    rollDir   = prefs.getInt("dR", 1);
    pitchDir  = prefs.getInt("dP", 1);
    accZeroSaved = prefs.getBool("aS", false);          // [추가]
    zeroAccRoll  = prefs.getFloat("aR", 0);
    zeroAccPitch = prefs.getFloat("aP", 0);
  }
  prefs.end();
  if (zeroSaved)
    Serial.printf("LOADED LEVEL ZERO: offR=%.2f offP=%.2f homeR=%d homeP=%d dirR=%d dirP=%d\n",
                  zeroRoll, zeroPitch, rollHome, pitchHome, rollDir, pitchDir);
  else
    Serial.println("NO SAVED LEVEL ZERO - 수평계로 맞춘 뒤 'g'를 누르세요");
}

void saveCalibration() {
  prefs.begin("level", false);
  prefs.putBool("saved", true);
  prefs.putFloat("zR", zeroRoll);
  prefs.putFloat("zP", zeroPitch);
  prefs.putInt("hR", rollHome);
  prefs.putInt("hP", pitchHome);
  prefs.putInt("dR", rollDir);
  prefs.putInt("dP", pitchDir);
  prefs.putBool("aS", accZeroSaved);                    // [추가]
  prefs.putFloat("aR", zeroAccRoll);
  prefs.putFloat("aP", zeroAccPitch);
  prefs.end();
}

void clearCalibration() {
  prefs.begin("level", false);
  prefs.clear();
  prefs.end();
  zeroSaved = false;
  directionChecked = false;
  zeroRoll = zeroPitch = 0;
  accZeroSaved = false; zeroAccRoll = zeroAccPitch = 0;   // [추가]
  Serial.println("=== SAVED LEVEL ZERO CLEARED ===");
}

// 8. BNO055 초기화
void initializeIMU() {
  Wire.begin(SDA_PIN, SCL_PIN);
  Wire.setClock(100000);
  Serial.println("Connecting BNO055...");
  if (!bno.begin()) {
    Serial.println("BNO055 NOT FOUND - Check wiring!");
    imuFound = false;
    return;
  }
  delay(50);
  bno.setExtCrystalUse(true);
  imuFound = true;
  imuInitTime = millis();
  Serial.println("BNO055 CONNECTED & READY");
}

// 9. Roll / Pitch 읽기
void readIMU() {
  if (!imuFound) return;
  imu::Quaternion quat = bno.getQuat();
  float qw = quat.w(), qx = quat.x(), qy = quat.y(), qz = quat.z();

  float calcRollRad = atan2f(2.0f * (qw*qx + qy*qz), 1.0f - 2.0f * (qx*qx + qy*qy));
  float value = constrain(2.0f * (qw*qy - qz*qx), -1.0f, 1.0f);
  float calcPitchRad = asinf(value);

  // 축 맞바꿈 (기구 장착 방향에 맞춤)
  float r = calcPitchRad * 180.0f / PI;
  float p = calcRollRad * 180.0f / PI;
  if (!isfinite(r) || !isfinite(p)) return;

  float qnorm = sqrtf(qw*qw + qx*qx + qy*qy + qz*qz);
  bool quatBad = fabsf(qnorm - 1.0f) > 0.1f;
  if (millis() - imuInitTime < IMU_WARMUP) {
    lastDataChangeTime = millis();
    if (quatBad) return;
  } else if (quatBad) {
    if (millis() - lastDataChangeTime > FROZEN_TIMEOUT) {
      Serial.printf("!!! IMU BAD DATA (|q|=%.3f) !!! REBOOTING IMU...\n", qnorm);
      initializeIMU();
      lastDataChangeTime = millis();
    }
    return;
  } else {
    lastDataChangeTime = millis();
  }

  rawRoll = r;
  rawPitch = p;
  if (!imuValid) {
    smoothRoll = rawRoll;
    smoothPitch = rawPitch;
  } else {
    smoothRoll += FILTER_ALPHA * (rawRoll - smoothRoll);
    smoothPitch += FILTER_ALPHA * (rawPitch - smoothPitch);
  }
  imuValid = true;
  lastIMU = millis();

  // [추가] 가속도계(중력 + 관성력 = 비력)를 읽어 퓨전각과 같은 축·부호로 변환
  //   센서 좌표: 정지·수평이면 (0, 0, +9.8). x축 회전(roll) → a_y = g·sinφ, y축 회전(pitch) → a_x = −g·sinθ
  //   기존 코드처럼 축을 맞바꿈: r ← 센서 pitch, p ← 센서 roll
  imu::Vector<3> acc = bno.getVector(Adafruit_BNO055::VECTOR_ACCELEROMETER);
  float ax = acc.x(), ay = acc.y(), az = acc.z();
  if (isfinite(ax) && isfinite(ay) && isfinite(az) && (ax*ax + ay*ay + az*az) > 1.0f) {
    float sRollDeg  = atan2f(ay, az) * 180.0f / PI;
    float sPitchDeg = atan2f(-ax, sqrtf(ay*ay + az*az)) * 180.0f / PI;
    float ar = sPitchDeg, ap = sRollDeg;                 // 축 맞바꿈 (readIMU 위쪽과 동일)
    unsigned long nowUs = micros();
    float dtA = accInit ? (nowUs - lastAccMicros) * 1e-6f : 0.0f;
    lastAccMicros = nowUs;
    // [v2 추가] 겉보기 중력 방향(세계 기준) = 퓨전각 − 가속도각 (필터 전 가속도각 사용 → 필터는 한 번만)
    float appR = (smoothRoll  - zeroRoll)  - (ar - zeroAccRoll);
    float appP = (smoothPitch - zeroPitch) - (ap - zeroAccPitch);
    if (!accInit) {
      accRoll = ar; accPitch = ap;
      appRollF = appR; appPitchF = appP;
      accInit = true;
    } else {
      float k = 1.0f - expf(-2.0f * PI * ACC_LPF_HZ * dtA);
      accRoll  += k * (ar - accRoll);
      accPitch += k * (ap - accPitch);
      float kApp = 1.0f - expf(-2.0f * PI * APP_LPF_HZ * dtA);
      appRollF  += kApp * (appR - appRollF);
      appPitchF += kApp * (appP - appPitchF);
    }
  }
}

bool imuFresh() {
  return imuFound && imuValid && (millis() - lastIMU < IMU_TIMEOUT);
}

// 11. 상태 표시
void showStatus() {
  Serial.println("-------------------------");
  Serial.printf("ROLL=%d (%s)  PITCH=%d (%s)  MODE=%d\n",
                rollPWM, rollReady ? "ON" : "OFF", pitchPWM, pitchReady ? "ON" : "OFF", autoMode);
  if (imuFresh()) {
    if (zeroSaved)
      Serial.printf("TRAY TILT (중력 기준) R=%.2f deg | P=%.2f deg\n", smoothRoll - zeroRoll, smoothPitch - zeroPitch);
    else
      Serial.printf("IMU RAW R=%.2f deg | P=%.2f deg (영점 미설정)\n", smoothRoll, smoothPitch);
    uint8_t sys, gyro, accel, mag;
    bno.getCalibration(&sys, &gyro, &accel, &mag);
    Serial.printf("BNO CALIB sys=%d gyro=%d accel=%d mag=%d (0~3)\n", sys, gyro, accel, mag);
  } else {
    Serial.println("IMU DATA NOT READY");
  }
  Serial.printf("HOME R=%d P=%d | INTEG R=%.0f P=%.0f us\n", rollHome, pitchHome, rollInteg, pitchInteg);
  Serial.printf("ZERO=%d DIR=%d (dirR=%d dirP=%d)\n", zeroSaved, directionChecked, rollDir, pitchDir);
  Serial.printf("MODE5 KP_A=%.2f KI_A=%.1f | MODE6 KP_J=%.2f KI_J=%.1f OUT_LPF=%s %.1f Hz\n",
                KP_A, KI_A, KP_J, KI_J, outLpfJOn ? "ON" : "OFF", OUT_LPF_J);
  Serial.printf("AXIS CTRL roll=%s pitch=%s | DEADBAND_A=%.1f deg\n",
                rollCtrlOn ? "ON" : "OFF", pitchCtrlOn ? "ON" : "OFF", DEADBAND_A);
  Serial.printf("SMOOTH %s | MAX_ACC=%.0f us/s^2 MAX_RATE=%.0f us/s SMOOTH_HZ=%.1f | vel R=%.0f P=%.0f us/s\n",
                smoothMotion ? "ON" : "OFF(v3)", MAX_ACC, MAX_RATE, SMOOTH_HZ, rollVel, pitchVel);
  Serial.printf("APP(겉보기 중력 방향) R=%.2f P=%.2f deg → a_est R=%.2f P=%.2f m/s^2\n",
                appRollF, appPitchF, G_ACC * tanf(appRollF * PI / 180.0f), G_ACC * tanf(appPitchF * PI / 180.0f));
  Serial.println("-------------------------");
}

// 12. 모터
void enableRoll() {
  if (rollReady) return;
  rollServo.setPeriodHertz(SERVO_HZ);
  rollServo.attach(ROLL_PIN, 500, 2500);
  rollPWM = zeroSaved ? rollHome : CENTER;    // 저장된 수평 위치에서 시작
  rollCmd = rollPWM;
  rollServo.writeMicroseconds(rollPWM);
  rollReady = true;
  Serial.println("ROLL ENABLED");
}

void enablePitch() {
  if (pitchReady) return;
  pitchServo.setPeriodHertz(SERVO_HZ);
  pitchServo.attach(PITCH_PIN, 500, 2500);
  pitchPWM = zeroSaved ? pitchHome : CENTER;
  pitchCmd = pitchPWM;
  pitchServo.writeMicroseconds(pitchPWM);
  pitchReady = true;
  Serial.println("PITCH ENABLED");
}

void moveRoll(int delta) {
  autoMode = 0;
  if (!rollReady) return;
  rollPWM = constrain(rollPWM + delta, MIN_PWM, MAX_PWM);
  rollServo.writeMicroseconds(rollPWM);
}

void movePitch(int delta) {
  autoMode = 0;
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

// 'g' : 트레이가 수평계로 0.0°인 상태에서 누르기 → 장착 오차 저장
void saveLevelZero() {
  autoMode = 0;
  if (!rollReady || !pitchReady || !imuFresh()) {
    Serial.println("서보 1,2 켜고 IMU 정상일 때 다시 누르세요");
    return;
  }
  zeroRoll = smoothRoll;
  zeroPitch = smoothPitch;
  zeroAccRoll = accRoll;        // [추가] 정지·수평 상태의 가속도각도 영점으로 저장
  zeroAccPitch = accPitch;
  accZeroSaved = true;
  rollHome = rollPWM;
  pitchHome = pitchPWM;
  zeroSaved = true;
  directionChecked = false;
  saveCalibration();
  Serial.printf("=== LEVEL ZERO SAVED TO FLASH: offR=%.2f offP=%.2f ===\n", zeroRoll, zeroPitch);
}

// [v4] 제어 시작 시 추종기를 현재 위치·정지 상태에서 시작 (켜는 순간 튀지 않게)
void resetMotionState() {
  rollCmd = rollProf = lastTargetRoll = rollPWM;
  pitchCmd = pitchProf = lastTargetPitch = pitchPWM;
  rollVel = pitchVel = 0;
}

void startLevelControl() {
  if (!rollReady || !pitchReady || !imuFresh() || !zeroSaved || !directionChecked) {
    Serial.println("조건 미충족: 1,2 / IMU / 영점(g) / 방향확인(v) 확인");
    return;
  }
  resetMotionState();   // [v4]
  rollInteg = 0;
  pitchInteg = 0;
  autoMode = 4;
  lastLevelUpdate = millis();
  Serial.println("=== ABSOLUTE LEVEL CONTROL ON (P+I) ===");
}

// [추가] 겉보기 중력 정렬 모드: 같은 P+I 구조, 오차만 가속도각(e)으로 바꿈
void startApparentControl() {
  if (!rollReady || !pitchReady || !imuFresh() || !zeroSaved || !directionChecked || !accZeroSaved) {
    Serial.println("조건 미충족: 1,2 / IMU / 영점(g, 이 버전에서 다시) / 방향확인(v) 확인");
    return;
  }
  resetMotionState();   // [v4]
  rollInteg = 0;
  pitchInteg = 0;
  autoMode = 5;
  lastLevelUpdate = millis();
  Serial.printf("=== APPARENT-GRAVITY CONTROL ON (KP=%.1f KI=%.1f) ===\n", KP_A, KI_A);
}

// [v2 추가] 겉보기 중력 모드 - 빠른 버전 (모드 6)
void startApparentFastControl() {
  if (!rollReady || !pitchReady || !imuFresh() || !zeroSaved || !directionChecked || !accZeroSaved) {
    Serial.println("조건 미충족: 1,2 / IMU / 영점(g) / 방향확인(v) 확인");
    return;
  }
  resetMotionState();   // [v4]
  rollInteg = 0;
  pitchInteg = 0;
  autoMode = 6;
  lastLevelUpdate = millis();
  Serial.printf("=== APPARENT-GRAVITY FAST CONTROL ON (KP=%.1f KI=%.1f OUT_LPF=%s %.1f Hz) ===\n",
                KP_J, KI_J, outLpfJOn ? "ON" : "OFF", OUT_LPF_J);
}

// 19. 수평 유지 P + I 제어
static float softDeadband(float e) {
  if (e > DEADBAND) return e - DEADBAND;
  if (e < -DEADBAND) return e + DEADBAND;
  return 0;
}

static float softDeadbandW(float e, float db) {
  if (e > db) return e - db;
  if (e < -db) return e + db;
  return 0;
}

// [v4] ① 속도·가속도 제한 추종기 (사다리꼴 속도 프로파일)
//   k       : 작은 오차에서의 추종 속도 [1/s] (= 2π·fc, v3의 1차 LPF와 같은 동작)
//   vStop   : 지금 오차에서 MAX_ACC로 감속해 목표에 딱 멈출 수 있는 최대 속도 → 미리 감속
static void followAxis(float target, float &pos, float &vel, float k, float dt) {
  float err = target - pos;
  float vDes = k * err;
  float vStop = sqrtf(2.0f * MAX_ACC * fabsf(err));
  vDes = constrain(vDes, -vStop, vStop);
  vDes = constrain(vDes, -MAX_RATE, MAX_RATE);
  float dv = constrain(vDes - vel, -MAX_ACC * dt, MAX_ACC * dt);   // 가속도 제한 = 툭 출발·툭 정지 방지
  vel += dv;
  pos += vel * dt;
}

void updateLevelControl(float dt) {
  if ((autoMode != 4 && autoMode != 5 && autoMode != 6) || !imuFresh()) return;

  // [추가] 모드 4 = 퓨전 자세각(지면 기준 수평), 모드 5 = 가속도각(겉보기 중력 기준)
  // [v2 추가] 모드 6 = 퓨전각 − 필터된 겉보기 중력 방향 (빠른 버전)
  bool apparent = (autoMode == 5);
  bool fast = (autoMode == 6);
  float kp = fast ? KP_J : (apparent ? KP_A : KP);
  float ki = fast ? KI_J : (apparent ? KI_A : KI);
  float rollError, pitchError;
  if (fast) {
    rollError  = softDeadbandW((smoothRoll  - zeroRoll)  - appRollF,  DEADBAND_A);
    pitchError = softDeadbandW((smoothPitch - zeroPitch) - appPitchF, DEADBAND_A);
  } else if (apparent) {
    rollError  = softDeadbandW(accRoll  - zeroAccRoll,  DEADBAND_A);
    pitchError = softDeadbandW(accPitch - zeroAccPitch, DEADBAND_A);
  } else {
    rollError  = softDeadband(smoothRoll  - zeroRoll);
    pitchError = softDeadband(smoothPitch - zeroPitch);
  }

  // 적분: 데드밴드 밖에서만 누적, 와인드업 제한
  // [v4] ③ 안티와인드업: 출력이 목표보다 AW_GAP 이상 뒤처져 있으면, 그 차이를 더 벌리는 방향의 적분은 멈춤
  //   적분이 목표를 미는 방향 = −dir·error (target = home − dir·(kp·e + integ))
  float gapR = lastTargetRoll  - rollCmd;
  float gapP = lastTargetPitch - pitchCmd;
  bool holdR = smoothMotion && fabsf(gapR) > AW_GAP && gapR * (-rollDir  * rollError)  > 0;
  bool holdP = smoothMotion && fabsf(gapP) > AW_GAP && gapP * (-pitchDir * pitchError) > 0;
  if (rollError != 0 && !holdR)  rollInteg  += ki * rollError  * dt;
  if (pitchError != 0 && !holdP) pitchInteg += ki * pitchError * dt;
  rollInteg  = constrain(rollInteg,  -(float)MAX_COMP, (float)MAX_COMP);
  pitchInteg = constrain(pitchInteg, -(float)MAX_COMP, (float)MAX_COMP);

  float targetRoll  = rollHome  - rollDir  * (kp * rollError  + rollInteg);
  float targetPitch = pitchHome - pitchDir * (kp * pitchError + pitchInteg);

  targetRoll  = constrain(targetRoll,  (float)max(MIN_PWM, rollHome - MAX_COMP),  (float)min(MAX_PWM, rollHome + MAX_COMP));
  targetPitch = constrain(targetPitch, (float)max(MIN_PWM, pitchHome - MAX_COMP), (float)min(MAX_PWM, pitchHome + MAX_COMP));

  // [v3] 축별 OFF: 그 축은 영점 위치로 돌아가 고정, 적분은 비움
  if (!rollCtrlOn)  { targetRoll  = rollHome;  rollInteg  = 0; }
  if (!pitchCtrlOn) { targetPitch = pitchHome; pitchInteg = 0; }

  lastTargetRoll = targetRoll;
  lastTargetPitch = targetPitch;

  if (smoothMotion) {
    // [v4] ① 사다리꼴 추종 → ② S커브 평활
    float k;
    if (fast) k = outLpfJOn ? 2.0f * PI * OUT_LPF_J : 1.0f / dt;
    else      k = 2.0f * PI * OUT_LPF_HZ;
    k = min(k, 1.0f / dt);                      // 이산 시간 안정 한계
    followAxis(targetRoll,  rollProf,  rollVel,  k, dt);
    followAxis(targetPitch, pitchProf, pitchVel, k, dt);
    float b = 1.0f - expf(-2.0f * PI * SMOOTH_HZ * dt);
    rollCmd  += b * (rollProf  - rollCmd);
    pitchCmd += b * (pitchProf - pitchCmd);
  } else {
    // v3 출력단 그대로 (비교용)
    // [v2] 모드 6은 출력 LPF를 따로 조절 ('<' '>' 'O')
    float a;
    if (fast) a = outLpfJOn ? 1.0f - expf(-2.0f * PI * OUT_LPF_J * dt) : 1.0f;
    else      a = 1.0f - expf(-2.0f * PI * OUT_LPF_HZ * dt);
    float maxStep = MAX_RATE * dt;
    rollCmd  += constrain(a * (targetRoll  - rollCmd),  -maxStep, maxStep);
    pitchCmd += constrain(a * (targetPitch - pitchCmd), -maxStep, maxStep);
    rollProf = rollCmd; pitchProf = pitchCmd;   // 'S'로 다시 켤 때 튀지 않게 상태 맞춰 둠
    rollVel = pitchVel = 0;
  }

  rollPWM = (int)lroundf(rollCmd);
  pitchPWM = (int)lroundf(pitchCmd);
  rollServo.writeMicroseconds(rollPWM);
  pitchServo.writeMicroseconds(pitchPWM);
}

void stopMotors() {
  autoMode = 0;
  if (rollReady) rollServo.detach();
  if (pitchReady) pitchServo.detach();
  rollReady = false;
  pitchReady = false;
  directionChecked = false;
  Serial.println("PWM OUTPUT DETACHED");
}

// 21. SETUP
void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println("\n==============================");
  Serial.println("ABSOLUTE LEVEL (P+I) + BNO055");
  Serial.println("==============================");
  initializeIMU();
  loadCalibration();
}

// 22. LOOP
void loop() {
  readIMU();

  while (Serial.available()) {
    char cmd = Serial.read();
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
        rollPWM = zeroSaved ? rollHome : CENTER;
        pitchPWM = zeroSaved ? pitchHome : CENTER;
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
      case 'G': clearCalibration(); break;
      case 'u': autoMode = 0; rollDir *= -1; directionChecked = false; break;
      case 'i': autoMode = 0; pitchDir *= -1; directionChecked = false; break;
      case 'v':
        if (zeroSaved && imuFresh()) {
          directionChecked = true;
          saveCalibration();   // 확인한 방향도 저장
          Serial.println("DIRECTIONS CONFIRMED & SAVED");
        }
        break;
      case 'h': startLevelControl(); break;
      case 'H': startApparentControl(); break;                       // [추가]
      case 'J': startApparentFastControl(); break;                   // [v2 추가]
      case 'l': logging = !logging;
                if (logging) Serial.println("t_ms,mode,fusR,fusP,accR,accP,appR,appP,aEstR,aEstP,pwmR,pwmP");
                break;
      case 'A': Serial.printf("FUSION R=%.2f P=%.2f | ACC R=%.2f P=%.2f | APP R=%.2f P=%.2f (정지 시 FUSION=ACC, APP≈0)\n",
                              smoothRoll - zeroRoll, smoothPitch - zeroPitch,
                              accRoll - zeroAccRoll, accPitch - zeroAccPitch,
                              appRollF, appPitchF); break;
      case '[': KP_A *= 0.8f;  KI_A *= 0.8f;  Serial.printf("KP_A=%.2f KI_A=%.1f\n", KP_A, KI_A); break;
      case ']': KP_A *= 1.25f; KI_A *= 1.25f; Serial.printf("KP_A=%.2f KI_A=%.1f\n", KP_A, KI_A); break;
      // [v2 추가] 모드 6 튜닝 (비례·적분 따로)
      case '(': KP_J *= 0.8f;  Serial.printf("KP_J=%.2f KI_J=%.1f\n", KP_J, KI_J); break;
      case ')': KP_J *= 1.25f; Serial.printf("KP_J=%.2f KI_J=%.1f\n", KP_J, KI_J); break;
      case '{': KI_J *= 0.8f;  Serial.printf("KP_J=%.2f KI_J=%.1f\n", KP_J, KI_J); break;
      case '}': KI_J *= 1.25f; Serial.printf("KP_J=%.2f KI_J=%.1f\n", KP_J, KI_J); break;
      case '<': OUT_LPF_J = max(1.0f, OUT_LPF_J * 0.8f);   Serial.printf("OUT_LPF_J=%.1f Hz\n", OUT_LPF_J); break;
      case '>': OUT_LPF_J = min(50.0f, OUT_LPF_J * 1.25f); Serial.printf("OUT_LPF_J=%.1f Hz\n", OUT_LPF_J); break;
      case 'O': outLpfJOn = !outLpfJOn; Serial.printf("OUT_LPF_J %s\n", outLpfJOn ? "ON" : "OFF (bypass)"); break;
      // [v3 추가] 축별 ON/OFF, 데드밴드
      case 'P': pitchCtrlOn = !pitchCtrlOn; Serial.printf("PITCH CTRL %s\n", pitchCtrlOn ? "ON" : "OFF (home 고정)"); break;
      case 'K': rollCtrlOn  = !rollCtrlOn;  Serial.printf("ROLL CTRL %s\n",  rollCtrlOn  ? "ON" : "OFF (home 고정)"); break;
      case ',': DEADBAND_A = max(0.1f, DEADBAND_A - 0.1f); Serial.printf("DEADBAND_A=%.1f deg\n", DEADBAND_A); break;
      case '.': DEADBAND_A = min(3.0f, DEADBAND_A + 0.1f); Serial.printf("DEADBAND_A=%.1f deg\n", DEADBAND_A); break;
      // [v4 추가] 부드러운 출발·정지
      case 'S': smoothMotion = !smoothMotion;
                if (smoothMotion) { rollProf = rollCmd; pitchProf = pitchCmd; rollVel = pitchVel = 0; }
                Serial.printf("SMOOTH MOTION %s\n", smoothMotion ? "ON" : "OFF (v3 출력)"); break;
      case '-': MAX_ACC = max(200.0f, MAX_ACC * 0.8f);    Serial.printf("MAX_ACC=%.0f us/s^2\n", MAX_ACC); break;
      case '=': MAX_ACC = min(50000.0f, MAX_ACC * 1.25f); Serial.printf("MAX_ACC=%.0f us/s^2\n", MAX_ACC); break;
      case '_': MAX_RATE = max(100.0f, MAX_RATE * 0.8f);  Serial.printf("MAX_RATE=%.0f us/s\n", MAX_RATE); break;
      case '+': MAX_RATE = min(5000.0f, MAX_RATE * 1.25f); Serial.printf("MAX_RATE=%.0f us/s\n", MAX_RATE); break;
      case 't': SMOOTH_HZ = max(1.0f, SMOOTH_HZ * 0.8f);   Serial.printf("SMOOTH_HZ=%.1f Hz\n", SMOOTH_HZ); break;
      case 'T': SMOOTH_HZ = min(50.0f, SMOOTH_HZ * 1.25f); Serial.printf("SMOOTH_HZ=%.1f Hz\n", SMOOTH_HZ); break;
      case 'x': autoMode = 0; Serial.println("AUTO STOPPED"); break;
      case 's': stopMotors(); break;
      case 'p': showStatus(); break;
    }
  }

  unsigned long now = millis();

  if (autoMode >= 1 && autoMode <= 3 && now - lastUpdate >= UPDATE_MS) {
    float dt = (now - lastUpdate) / 1000.0f;
    lastUpdate = now;
    phase += 2.0f * PI * dt / PERIODS[periodIdx];
    if (phase >= 2.0f * PI) phase = fmodf(phase, 2.0f * PI);
    int offset = (int)lroundf(amp * sinf(phase));
    int pwm = constrain(CENTER + offset, MIN_PWM, MAX_PWM);
    if (autoMode == 1 || autoMode == 3) { rollPWM = pwm; rollServo.writeMicroseconds(rollPWM); }
    if (autoMode == 2 || autoMode == 3) { pitchPWM = pwm; pitchServo.writeMicroseconds(pitchPWM); }
  }

  if ((autoMode == 4 || autoMode == 5 || autoMode == 6) && now - lastLevelUpdate >= LEVEL_MS) {
    float dt = (now - lastLevelUpdate) / 1000.0f;
    lastLevelUpdate = now;
    updateLevelControl(dt);
  }

  static unsigned long lastPrint = 0;
  if ((autoMode == 4 || autoMode == 5 || autoMode == 6) && !logging && now - lastPrint >= 500) {
    lastPrint = now;
    showStatus();
  }

  // [추가] CSV 로그: 모든 모드(고정 포함)에서 같은 지표를 남김
  //   accR/accP = 트레이 기준 겉보기 중력 각 = 액면이 컵에 대해 기우는 각(준정적) → 핵심 평가 지표
  //   fusR/fusP = 지면 기준 트레이 각
  static unsigned long lastLog = 0;
  if (logging && imuFresh() && now - lastLog >= 20) {
    lastLog = now;
    //   [v2] appR/appP = 세계 기준 겉보기 중력 방향 [deg], aEstR/aEstP = g·tan(app) = 추정 로봇 가속도 [m/s^2]
    Serial.printf("%lu,%d,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.3f,%.3f,%d,%d\n", now, autoMode,
                  smoothRoll - zeroRoll, smoothPitch - zeroPitch,
                  accRoll - zeroAccRoll, accPitch - zeroAccPitch,
                  appRollF, appPitchF,
                  G_ACC * tanf(appRollF * PI / 180.0f), G_ACC * tanf(appPitchF * PI / 180.0f),
                  rollPWM, pitchPWM);
  }
}

/* ---------------------------------------------------------------- [v2] 퓨전각 끌림 확인 (모드 6 쓰기 전 1회)
 * 모드 6은 "퓨전각 = 트레이의 실제 기울기"라는 가정에 기대고 있음.
 * BNO055 퓨전은 내부적으로 가속도계로 기울기를 보정하므로, 긴 가속 중 겉보기 중력 쪽으로 끌려갈 수 있음.
 * 1. 서보 1,2 켜고 'z'(수평 위치 고정), 자동 제어는 끈 상태(모드 0)
 * 2. 'l'로 로그 시작 → 평지에서 로봇(또는 카트)을 급정거시킴 (1.5 m/s² 이상, 1초 정도 감속)
 * 3. 로그 확인
 *    - fusR/fusP: 0 근처(±1° 이내)에 머물러야 정상  → 모드 6 사용 가능
 *    - accR/accP, appR/appP: 감속하는 동안 수 ° 변해야 정상 (1.5 m/s² ≈ 8.7°)
 *    - fusP가 accP를 따라 수 ° 움직이면 퓨전이 끌린 것 → 모드 6 대신 모드 5 사용
 */

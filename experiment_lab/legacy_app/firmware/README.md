# 앱용 ESP32-S3 펌웨어

`esp32_new_ver/esp32_new_ver.ino`는 제공받은 기존 스케치이며 보존한다. 새 스케치는 `esp32_app_control/esp32_app_control.ino`다. 보드가 연결되지 않아 업로드/실물 동작은 수행하지 않았다.

## 포함한 기능

- 기존 100 Hz BNO055 수평 유지와 PWM 출력 필터/변화율 제한을 유지한다.
- 기존 `1/2` 서보 활성화, `g` 영점, `v` 방향 확인, `h` 수평 유지, `x` 보정 중지/PWM 유지, `s` 출력 해제와 `Q/W/E/R` 10 μs 미세 조정을 지원한다.
- 상태에 `PROTO=2`, `TARGET R=... P=...`, `APP_TIMEOUT=...`를 추가한다. 수평 유지는 MODE=4, 앱 예측 목표 추종은 MODE=5다. 앱의 1/2/3 모드 번호와 펌웨어 내부 모드 번호는 다르다.
- 새 목표 명령은 `@T,<roll_deg>,<pitch_deg>\n`이다. 각도는 저장한 영점 대비 **트레이 IMU 목표각**이며 서보 축의 실측 회전각이 아니다. 범위 ±8°, 두 서보 활성화·정상 IMU·영점·방향 확인 후만 받는다.
- 500 ms 목표 수신 만료는 MODE=4 / 목표 0°로 복귀한다. IMU 만료는 보정을 멈추고 마지막 PWM을 유지한다. 자동 재활성화/서보 부팅 구동은 없다.
- `@` 이후는 줄 전체를 받은 후 해석한다. 100 ms 이상 미완성/넘친 프레임은 줄바꿈까지 버린다. 호스트는 PROTO=2 확인 후에만 줄바꿈으로 재동기화한다. 기존 펌웨어에는 다중 바이트 목표를 전송하지 않는다.
- 새 펌웨어의 미세 조정/서보 재활성화 뒤에는 영점을 다시 저장하고 방향을 확인한다.

## 기존 배선과 센서

GPIO: Roll 18, Pitch 17, I²C SDA 8/SCL 9. BNO055 주소 0x29이며 기존 축 교환을 보존한다. BNO085용 드라이버가 아니다. 서보 출력 100 Hz, PWM 1100~1900 μs도 기존 값이며 실제 서보 허용 범위·부하 반응은 별도 확인 대상이다. 앱의 예측 축 부호/맞바꿈과 ESP32의 서보 피드백 부호는 각각 실제 장착 방향에 맞춰야 한다.

## 준비·빌드·업로드

Arduino IDE에서 새 `.ino`를 열고 사용 중인 ESP32-S3 보드와 실제 USB 방식/포트를 선택해 업로드한다. 네이티브 USB CDC를 사용하는 경우 USB CDC On Boot를 Enabled로 설정한다. USB-UART 브리지 포트를 사용하는 보드는 그 구성에 맞게 설정해야 한다. 연결 후 GUI에 PROTO=2가 표시되는지 확인한다. 컴파일된 바이너리를 보드 구성 확인 없이 자동 업로드하지 않는다.

이 노트북에서의 빌드 명령:

```bash
bash ~/d420_ws/scenario_lab/firmware/build.sh
```

이 스크립트는 공식 Arduino CLI와 아래 고정 라이브러리를 임시 도구 폴더에 설치하고 **컴파일만** 한다. 시리얼 포트를 열거나 업로드하지 않는다. 보드 설정은 `esp32:esp32:esp32s3:CDCOnBoot=cdc`이며 일반 ESP32-S3 Dev Module + 네이티브 USB CDC의 빌드 검증이다. 사용자 보드의 USB 변형·플래시/PSRAM 구성을 식별한 결과는 아니다.

- ESP32 Arduino core 3.3.0
- ESP32Servo 3.0.9
- Adafruit BNO055 1.6.4
- 설치된 종속 라이브러리/도구 버전: `compiled_esp32s3/*_versions.json`

바이너리와 ELF/MAP는 `compiled_esp32s3/`, 최종 컴파일 로그는 `build_verified.log`에 저장한다. 소스/바이너리 해시는 `build_manifest.json`에 기록한다. 임시 도구는 `/tmp/hongik-arduino-build`에 있으며 `HONGIK_ARDUINO_ROOT`로 위치를 지정할 수 있다.

공식 설치 참고: [Arduino CLI](https://docs.arduino.cc/arduino-cli/installation), [Espressif Arduino ESP32](https://docs.espressif.com/projects/arduino-esp32/en/latest/installing.html).

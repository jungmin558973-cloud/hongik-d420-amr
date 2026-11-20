# ESP32-S3를 노트북에서 준비하고 Jetson에 연결하기

USB CDC On Boot는 노트북의 Arduino IDE에서 펌웨어를 빌드·업로드할 때 설정하는 옵션입니다. Jetson에서 켜는 옵션이 아닙니다. 설정을 Enabled로 바꾸기만 해서는 현재 보드의 프로그램이 바뀌지 않으며, 새로 업로드해야 반영됩니다. 업로드된 프로그램은 전원을 껐다 켜도 유지됩니다.

이 안내는 프로젝트의 ESP32-S3 + BNO055 + 네이티브 USB 구성 기준입니다. 일반 ESP32나 UART/CP210x/CH340 단자로 연결하는 경우 설정이 다릅니다.

## 1. 먼저 현재 펌웨어 확인

서보 외부 전원을 끈 상태에서 ESP32를 데이터 USB 케이블로 노트북에 연결합니다. Arduino IDE의 도구 → 포트에서 새 COM 포트를 선택하고 시리얼 모니터를 115200으로 엽니다. 소문자 p를 전송합니다.

다음이 나오면 앱용 예측 펌웨어가 이미 들어 있습니다. 단순 연결을 위해 다시 업로드할 필요는 없습니다.

```text
PROTO=2
FIRMWARE=APPARENT_V4_FF
PREDICT_MODE=7
```

## 2. 업로드가 필요한 경우

1. Arduino IDE 보드 매니저에서 esp32 by Espressif Systems를 설치합니다. 이 소스는 Arduino-ESP32 3.x의 LEDC API를 사용하며 프로젝트에는 3.3.12 빌드 확인 기록이 있습니다.
2. 보드 모델을 실제 ESP32-S3 모델로 선택합니다. 해당 모델이 없을 때 ESP32S3 Dev Module을 사용하되 실제 Flash/PSRAM 구성을 먼저 확인하세요. 기존 영점 보존을 위해 전체 플래시 삭제는 사용하지 않습니다.
3. 라이브러리 매니저에서 Adafruit BNO055를 설치하고 필요한 의존 라이브러리도 설치합니다.
4. 파일 → 열기로 아래 스케치를 엽니다.
   `legacy_app/firmware/esp32_level_predictive_v4/esp32_level_predictive_v4.ino`
5. ESP32-S3의 네이티브 USB 단자에 연결한 경우 도구 메뉴에서 다음을 선택합니다.

| 메뉴 | 설정 |
|---|---|
| Board | 실제 ESP32-S3 보드 모델 |
| USB CDC On Boot | Enabled |
| Upload Mode | UART0 / Hardware CDC (메뉴에 있는 경우) |
| Port | ESP32를 연결했을 때 나타나는 COM 포트 |
| Erase All Flash Before Sketch Upload | Disabled (메뉴에 있는 경우) |
| Flash Size / PSRAM / Partition Scheme | 보드 실물과 기존 구성에 맞는 설정; 임의 변경하지 않음 |

USB-UART 단자에 연결한 경우에는 위 네이티브 USB CDC 설정을 그대로 적용하지 마세요. 동일 보드에 USB와 UART 단자가 모두 있으면 사용할 단자와 빌드 설정을 일치시켜야 합니다.

6. 확인(체크) 버튼으로 컴파일한 다음 업로드(오른쪽 화살표)를 누릅니다. 컴파일만으로 보드에 저장되지 않습니다.
7. 최초 업로드가 연결 대기에서 멈추면 BOOT를 누른 채 RESET을 한 번 눌렀다 떼고 BOOT를 놓아 다운로드 모드로 진입한 뒤 새 포트를 선택해 업로드합니다. 첫 CDC 업로드 후 RESET이 한 번 필요할 수 있습니다.
8. 업로드 후 시리얼 모니터에서 115200으로 소문자 p를 보내 위의 세 항목을 확인합니다. 포트 번호가 바뀌었다면 새 COM 포트를 다시 선택합니다.

## 3. Jetson으로 옮기기

1. 노트북 시리얼 모니터를 닫고 ESP32 USB를 분리합니다.
2. Jetson은 정상 전원으로 켜고 ESP32를 데이터 USB 케이블로 연결합니다. 정품 Orin Nano 개발보드는 USB-C Host를 지원하므로 C to C도 사용할 수 있습니다. 처음 문제를 분리할 때는 USB-A to C로 비교할 수도 있습니다.
3. Jetson 터미널에서 연결 전후를 비교합니다.

```bash
ls -l /dev/serial/by-id/
python3 -m serial.tools.list_ports -v
```

네이티브 USB CDC는 /dev/ttyACM 계열, USB-UART는 /dev/ttyUSB 계열로 나타날 수 있으나 번호만으로 ESP32라고 판단하지 않습니다. USB 식별자·시리얼 번호와 연결 전후 변화를 확인해 앱의 트레이 장치로 지정해야 합니다. LiDAR CP2102와 차체 CH340 포트는 선택하지 않습니다. 가능하면 /dev/serial/by-id 경로를 사용합니다.

4. 앱에서 트레이 연결 상태와 실제 Roll/Pitch 변화·IMU 수신을 확인합니다. PROTO=2만 나오는 것은 센서 정상·교정 완료와 다릅니다. IMU를 손으로 기울였을 때 값이 실제로 바뀌어야 합니다.

USB는 ESP32 전원·통신에 쓰며 서보는 기존 규격의 외부 전원을 사용합니다. 펌웨어 업로드/USB 연결만으로 서보 자동 보정을 시작하지 않습니다. 첫 단계는 통신·IMU 수신 확인까지입니다. 자동 수평·예측 보상은 영점·축 방향·서보 전원/배선 확인 후 별도로 확인합니다.

## 이 펌웨어의 배선

| 항목 | 설정 |
|---|---|
| 통신 | 115200 |
| IMU | BNO055, I2C 주소 0x29 |
| IMU SDA/SCL | GPIO8 / GPIO9 |
| 서보 Roll/Pitch 신호 | GPIO18 / GPIO17 |

실제 센서가 BNO085이거나 다른 핀에 연결되어 있으면 이 스케치를 그대로 사용하지 않습니다. BNO055가 0x28로 배선된 경우 주소 설정을 맞춰야 합니다.

공식 설명:
- Espressif USB CDC: https://docs.espressif.com/projects/arduino-esp32/en/latest/tutorials/cdc_dfu_flash.html
- NVIDIA Orin Nano 포트: https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/hardware_layout.html

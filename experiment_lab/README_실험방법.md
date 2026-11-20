# 현재 사용법: 녹화 우선 실험실

최신 메인 화면·자동 회차 저장·사후 분석 방법은 [RECORDING_FIRST_사용법.md](RECORDING_FIRST_사용법.md)를 우선 확인하세요. 아래 내용은 이전 개발 단계의 상세 설명을 포함합니다. 실차 연결은 현재 상태 표시로 확인해야 하며, 검증되지 않은 동작은 계속 차단합니다.

# HONGIK CAFE D420 실험실

이 패키지는 기존 앱을 보존한 독립 확장이다. 실제 연결은 `Connect-Real-Jetson.cmd`로 실행하며, 열린 로컬 창에 SSH 암호를 직접 입력한다. 현재 Jetson 기록 서비스와 C920 장치 1이 연결되어 있다. 연결 창은 SSH 터널 유지용으로 열어 둔다. **처음에는 `Start-Mock.cmd`만 실행한다.** 실차 명령은 개발 중 전송하지 않았다. 후진은 복구용이며 전진·제동·정지·안정화 데이터만 KPI/비교/추천에 사용한다.

## Windows 모의 실험

1. Python 3.12 64-bit를 준비하고 `Start-Mock.cmd`를 더블클릭한다. 첫 실행에 가상환경과 고정된 직접 의존성을 설치한다. 한글 명령 BAT 인코딩 문제를 피하도록 실행기 명령은 영문 ASCII로 작성했다.
2. 브라우저의 실험실에서 `MOCK`와 합성 영상 워터마크를 확인한다. A~F, 1.5/2m, 20/30/40cm/s를 모의 시험할 수 있다.
3. 액면 ROI는 수면이 존재하는 컵 내부를, 앵커는 컵의 질감 있는 림/표식을 따로 드래그하고 적용한다. 합성 영상에는 초기 ROI가 제공된다.
4. 현장 확인 항목을 모두 체크 → ARM → 선택 실험 시작. 기록 ACK → 프리롤 → 전진 → 감속 → 실제 정지 확인 → 안정화 측정 → 저장 → 선택한 원점 복구 순서다. 매 실험 ARM과 시작이 필요하다.
5. 결과 표에서 영상, 동기 CSV, 그래프, 보고서를 연다. 복귀가 진행 중이어도 앞선 측정 결과 저장은 별도 스레드에서 이루어진다. 복귀 감사 이벤트는 `experiments/_recovery/`에만 기록한다.
6. `영상·로그 기록만`은 자동 구동 명령을 보내지 않는다. `일시중단 / 기록 종료` 또는 STOP으로 종료한다. STOP은 기록 전용 모드에서도 독립 정지 요청을 우선한다.
7. 서버 종료는 콘솔 Ctrl+C. 실행 중 종료·화면 heartbeat 단절은 자동 실험을 중단한다. 다음 시작 시 미완료 기록은 ABORTED로 복구한다.

CLI 대안:

```powershell
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m lab.server --mock-camera
```

`Run-Tests.cmd` 또는 `python -m pytest tests -q`로 모의 검사를 실행한다. 실행된 환경 전체 버전은 `requirements-windows-tested.lock.txt`에 제공한다. Jetson은 시스템 ROS Python을 사용하므로 Windows lock을 설치하지 않는다.

## C920e 연결 및 영상 설정

- 카메라는 Windows USB에 연결하고 정지 구간을 향해 고정한다. `auto`/`DSHOW`를 선택하면 실제 장치 이름에서 C920을 찾는다. C920이 없으면 내장 카메라로 대신 연결하지 않는다. 이 노트북은 0=ASUS FHD webcam, 1=HD Pro Webcam C920이다. 현재 native 640×480/약30fps가 확인됐다. 강제 해상도 설정에서 드라이버가 멈추어 기본 설정에서는 형식을 강제하지 않는다. 720p/FHD는 별도 확인이 필요하다. 사용자가 실기기 연결을 요청한 뒤 실제 장치를 확인했다. 최초 장치 0 오선택은 기록에서 정정했다.
- Teams/Zoom/브라우저 카메라 점유를 해제하고 Windows 카메라 개인정보 권한을 확인한다. 열기/읽기 오류는 원인과 장치 정보를 표시한다. 8초 무응답 시 워커만 종료하며 서버를 유지한다. UI의 연결 버튼으로 재시작한다.
- 카메라 FPS, 캡처 read 소요시간, 처리시간, 미리보기 수신 지연은 서로 다른 값이다. 드라이버 read 시각은 노출 시각과 같지 않다. 광학 마커를 통한 지연 검증 전 이를 물리 반응시간으로 해석하지 않는다.
- 입력란에 로컬 `.mp4/.avi/.mkv/.mov` 전체 경로를 넣으면 파일 replay가 된다. 시점 슬라이더로 이동 → 컵 등장 시각 지정 → 액면/앵커 드래그 → 적용. 기록 중 seek/ROI 변경은 차단된다.
- 기본은 액체 색상 HSV 검출이다. 투명한 물은 조명·배경과 meniscus 명암을 확인하고 `method=edge` 모드를 ROI API에 지정할 수 있다. 드래그 영역 안에 굴절된 격자나 배경선이 있으면 제거하고 오버레이를 직접 검증해야 한다.
- 컵 앵커 손실·가림·크기 변화·ROI 이탈·액면 지지 부족은 invalid/null로 남긴다. 추적 실패 후 무관한 배경 전체를 자동 검색하지 않는다. 사용자가 다시 앵커를 지정한다. mm 값은 `calibration_verified=true`와 같은 깊이 평면에서 확인한 `mm_per_px`가 있을 때만 프레임 로그에 기록된다. 기본 KPI는 px이다.
- MP4는 고정 프레임률 컨테이너다. 물리 시간 분석에는 `camera_frames.csv`의 프레임별 UTC/monotonic과 `synchronized.csv`의 매칭 오차를 사용한다. 프레임 드롭 시 영상 재생시간과 물리 경과시간은 다를 수 있다.

## Jetson: 먼저 기록 전용 연결

1. 패키지를 Jetson의 새 폴더에 복사한다. 원본 프로젝트·서비스·ESP32 펌웨어를 덮어쓰지 않는다.
2. `bash scripts/install_jetson.sh`로 시스템 의존성을 설치한다. `bash scripts/inspect_jetson.sh`로 실제 ROS 토픽·서비스·포트 소유자를 조사한다. **ROS_DOMAIN_ID=120**이다.
3. Windows/Jetson의 시계를 NTP/chrony로 맞춘다. 서로 다른 monotonic 값은 직접 비교하지 않는다. 연결 시 9회 왕복으로 `Jetson UTC - Windows UTC` 오프셋과 불확도를 저장한다. 종료 시 재측정하여 20ms 초과 변화를 품질 저하로 표시한다.
4. 임의 난수 토큰을 한 번 생성하고 양쪽 환경변수 `D420_AGENT_TOKEN`에 설정한다. 토큰은 코드/ZIP/로그에 기록하지 않는다. 예: `python -c "import secrets; print(secrets.token_urlsafe(32))"`의 출력은 개인적으로 보관한다.
5. Jetson에서 `bash scripts/start_agent.sh`. 이는 ROS 수신/기록 서비스이며 USB 포트를 열지 않는다. 기존 센서/오도메트리 서비스가 이미 정상이어야 한다.
6. 기존 `connection.json`의 Jetson 주소와 SSH 사용자 설정을 사용하여 loopback 터널 `-L 18866:127.0.0.1:18866`을 기존 SSH 접속에 추가한다. 예시 접속 IP/계정은 하드코딩하지 않는다. 기존 원격주행 터널/포트는 기존 실행기로 그대로 유지한다.
7. Windows 환경변수 `D420_AGENT_TOKEN`, 필요하면 `D420_AGENT_URL=http://127.0.0.1:18866`을 설정하고 `Start-Real-Recording.cmd`를 실행한다. 카메라를 연결한 후 기록 전용으로 검증한다. 확장 제어 브리지가 없어도 `/odom`과 발견된 ROS 토픽의 원시 수집은 가능하다.
8. Jetson은 발견한 실제 타입만 구독하며 rosbag2 CDR과 원시 수신 로그를 로컬에 쓴다. 네트워크 10~50Hz 상태 수집은 원시 ROS 기록의 대체가 아니다. 새로운 토픽이 실험 중 생기면 다음 실험부터 bag inventory에 포함된다. 큐 초과/쓰기 오류는 실험 중단 사유다.

## 실차 제어 확장 설치 및 검증

이 단계는 아직 실차 검증되지 않았다. `SAFETY_CHECKLIST.md`와 `TOPIC_API_MAPPING.md`를 확인한다.

1. 기존 서비스의 `systemctl --user cat hongik-scenarios.service`와 실행 중인 시리얼 소유자 경로를 보존한다. 센서 스택은 유지하고, 해당 시리얼 소유자만 현장에서 교체하도록 서비스/launch 사본을 준비한다. 제공 스크립트는 기존 서비스를 자동 변경하지 않는다.
2. `validation.example.json`을 소스 밖의 `validation.local.json`으로 복사한다. 기본은 .12m/s·.60m, S-curve false, OFF만, reverse false. 현장 검증 전 더 큰 값으로 올리지 않는다.
3. 기존 포트 소유자가 정지한 것을 확인한 후 현장 사람이 `D420_VALIDATION_FILE`을 지정하고 `bash scripts/start_commissioning_bridge.sh --operator-at-robot <실제 기존 포트 경로>`를 실행한다. 포트가 사용 중이면 거절한다. 기존 모터 패킷·Decoder를 재사용한다. 자동 ARM/자동 이동은 없다.
4. 최초 UI 설정은 **A / 0.3m / .12m/s / 복구 체크 해제**. 차륜을 들어 STOP·watchdog·소유권 충돌부터 확인한 다음 빈 통로의 짧은 직선으로 진행한다.
5. 후진은 후방 LiDAR 유효범위·장애물·최대시간·원점 오차를 별도 검증한 뒤 reverse만 승인한다. 전진 1.5m/2m 검증도 각각 기록한다. 20 → 30 → 40cm/s는 순차 현장 검증 후에만 허용한다. 브리지 `D420_BRIDGE_MAX_SPEED`도 승인 한도와 일치해야 한다.
6. LEVEL/PREDICTIVE는 기존 앱에서 서보 전원·영점·방향·PROTO=2·실제 mode와 route ACK를 검증한 뒤 해당 tray_modes에만 추가한다. 새 앱은 서보를 자동 켜거나 영점을 저장하지 않는다. OFF는 기존 PWM 유지다. 복귀는 예측 보상을 사용하지 않는다.
7. 수동 조종은 상단 기존 원격주행 화면에서 기존 dead-man을 사용한다. 자동 실험과 동시에 명령하면 신규 실험은 중단된다. STOP latch를 해제하여 기존 소유자로 복귀하는 방법은 별도 `release` API/현장 중립 확인을 사용한다. 기존 앱의 속도 선택 범위는 원본 그대로이므로 기존 운영 규칙을 유지한다.

## 저장 · Drive · 복구

각 실험 폴더에는 `config.json`, `roi.json`, `clock.json`, `manifest.json`, `events.jsonl`, 원본/오버레이 MP4, 프레임 JSONL/CSV, `robot_telemetry.jsonl`, 실제 연동 시 `robot_raw.jsonl`/`rosbag2/`/토픽 inventory, `synchronized.csv`, `metrics.json`, 4종 PNG, `report.html`이 저장된다. 존재하지 않는 센서값/실측 서보각/물리 브레이크 피드백은 null/미지원이다. 모의 데이터에는 simulated가 표시되며 가짜 rosbag을 만들지 않는다.

먼저 파일 close/fsync와 SHA-256 manifest를 만든 뒤 SQLite와 업로드 큐를 갱신한다. 업로드는 별도 스레드에서 실행된다. 인증 실패/통신 단절은 지수 backoff로 재시도하고 로컬 파일을 삭제하지 않는다.

Google Cloud에서 Desktop OAuth 클라이언트와 Drive API를 준비하고 클라이언트 JSON은 소스 밖에 둔다:

```powershell
.venv\Scripts\python.exe -m lab.drive --authorize C:\private\oauth_client.json
$env:D420_DRIVE_FOLDER_ID = '<user selected raw folder id>'
```

OAuth 토큰은 OS keyring에 보관한다. `drive.file` 권한으로 앱이 접근할 수 있는 폴더를 선택한다. 업로드는 run_id/appProperties로 중복을 피하고 파일 MD5/크기 원격 확인과 로컬 SHA-256을 검증한다. 실제 계정 업로드 성공은 현장에서 확인해야 한다. API 인증이나 목적 폴더가 없으면 업로드 대기/재시도 상태가 정상이다.

Windows 재시작 시 RECORDING/SAVING 행은 ABORTED로 복구한다. MP4가 워커/OS 강제 종료로 손상됐으면 원본을 보존하며 복구 성공을 보장하지 않는다. Jetson 자료 다운로드만 실패한 경우 Jetson 원본은 유지된다. `python scripts/recover_jetson.py --run-id <RUN_ID>`로 재수집·재분석·업로드 재등록한다.

## 비교 · 추천 해석

거리, 실제 최고속도 2cm/s 범위, 컵·수위·뚜껑·직경·질량·카메라/ROI, 실제 정지위치 5cm 범위, S-curve/트레이별로 그룹을 분리한다. 모의와 실차는 섞지 않는다. 평균/중앙값/표준편차/표본수/IQR 이상치를 함께 제공한다. 소수 표본으로 최적이라고 단정하지 않는다.

추천은 품질 80% 이상, 정지 오차 ±5cm 이내의 비교 가능한 실제 완료 시험 3회 이상이 있어야 한다. 가속도·감속도·jerk·lead의 보수적 유한 격자를 탐색하고 정지여유와 주행시간을 확인한다. 예측 효과는 검증 전 가설로 표시한다. **적용 승인도 설정 초안만 변경하며 모터에 자동 적용하지 않는다.** 최종 ARM/시작은 다시 필요하다. Bayesian optimization/RL은 구현 완료라고 주장하지 않는다.

## 롤백

Windows 신규 서버/Jetson agent/확장 브리지를 현장에서 정지 → 기존 서비스/launch 경로 복원 → 기존 단일 시리얼 소유자만 재시작. 원본 폴더는 변경하지 않았다. 변경 전 ZIP과 manifest는 상위 `.d420_backup/`와 본 패키지에 있다. 실험 폴더와 keyring OAuth는 소스 롤백과 별개로 보존한다.

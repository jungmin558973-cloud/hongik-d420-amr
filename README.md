# HONGIK D420 AMR · 주행·트레이 실험

최신 배포 **09 / 2026-11-16**. Jetson 로컬 주행 + ESP32 트레이 + Windows 연결 앱입니다.

- [Windows 전체 설치 ZIP](https://drive.google.com/file/d/1kZRE0z25H4UatxLoMG0xzAzMzVb4vaU9/view?usp=drivesdk)
- [실험 세팅·160cm 정지·회전 변경 안내](scenario_lab/EXPERIMENT_SETUP_20261116.md)
- [15조건 실험과 센서·DB 설명](scenario_lab/EXPERIMENT_15_CASES_20261116.md)

## 동작

별도 실험 세팅 화면에서 20/30/40cm/s, S-curve 0~100%, 회전 최고 0.4/0.6/0.8rad/s를 선택합니다. 160cm 목표 정지 시험은 현재 속도와 제동 모델로 정지 명령 지점을 계산하고 실제 정지 오차를 기록합니다. 이후 동일 속도 정상 급정지 시험의 실측 제동거리로 보정합니다. 첫 회의 정확 정지는 보장하지 않습니다.

L자는 기본 3.2m+30cm 후 우90° → 2.2m입니다. 무보정/수평/능동/S-curve/능동+S-curve × 3속도, 총 15조건 버튼도 제공합니다. 무보정은 영점 홈 PWM 고정입니다.

수락된 L자는 Jetson에서 실행·기록하므로 Windows 화면/연결 끊김만으로 취소하지 않습니다. STOP과 장애물/센서/USB 오류 정지는 유지합니다. 작은 경로 오차 복귀를 지원하며 큰 장애물의 전역 우회는 아직 구현하지 않았습니다. 토크를 직접 설정하지 않고 각속도 명령을 사용합니다.

실험별 SQLite DB, CSV/JSON 내보내기, 속도·가감속·명령 jerk·트레이 측정/목표각·PWM·정지 사유를 제공합니다. 기존 ESP32 보고 약 2Hz는 급격한 최대각을 놓칠 수 있습니다. 물 유출량·실제 서보 엔코더·차체 가속도 IMU를 실측했다고 주장하지 않습니다.

## 설치와 검증

전체 ZIP을 새 폴더에 해제 → 로봇 정지 → Setup-Jetson.cmd → Install-HongikCafe.cmd. GitHub 소스에는 번들 CAD/영상/외부 라이브러리와 payload 바이너리를 포함하지 않습니다. GitHub ZIP만으로 설치하지 말고 위 전체 설치 ZIP을 사용하세요. 기존 Jetson ROS Humble/D420 환경이 필요합니다. ESP32 펌웨어는 자동 업로드하지 않습니다.

자동 시험 **49개**, Edge 브라우저 시험 **1개** 통과. 실제 배포·제동거리·새 회전 속도·트레이 성능은 미검증입니다.

## 공동 작업

main 최신본에서 작업 브랜치를 만들고 PR로 공유하세요. 실험 DB(runs/), 비밀번호·개인 키·토큰은 커밋하지 않습니다. main 갱신만으로 로봇이 자동 배포/출발하지 않습니다. 제어 변경은 단위·모의시험 후 낮은 속도 실물 검증을 거칩니다.

소스: scenario_lab/ · Windows 연결/설치: windows/. 로컬 검증 예: python -m unittest discover -s scenario_lab/tests -v (aiohttp, Pillow, PyYAML 등 기존 앱 의존성 필요).

# 실제 소스 조사 및 적용 설계 — 2026-11-20 (Asia/Seoul)

## 조사 근거

- 사용자가 개발 요구사항으로 지정한 첨부 문서 전체를 `REQUIREMENTS_ORIGINAL.md`에 보존했다. 개발 중 추가 지시: **후진은 실험 데이터가 아닌 원점 복구용**이다. 따라서 전진/정지/안정화까지 측정하고, 복귀는 별도의 안전 감사 이벤트만 기록한다.
- Google Drive 폴더 `1cjH8Q4XZI-BAKNUK3quQK2gk_RwC477n`를 연결 도구로 직접 조회했다. `01_D420_전체소스코드.zip` 5,230,354 bytes, 수정 2026-11-13, `09_HONGIK_CAFE_Experiment_Setup_Windows_2026-11-16.zip` 2,497,982 bytes 및 L자 안내 파일을 확인했다.
- 전체 소스 ZIP을 내려받아 `.d420_backup/drive_full_source/`에서 실제 `scenario_lab`, `src`, `external/d420_ros_integrated`, `config`, `diagnostics`, `maps` 구조를 확인했다. 원본 다운로드 ZIP도 `.d420_backup/drive_full_source.zip`에 남겼다.
- 로컬 `HONGIK_CAFE_L_PREDICTIVE/app`의 모든 배포 파일은 로컬 최신 `10_HONGIK_CAFE_Integrated_Experiment_Windows_2026-11-16.zip`의 내부 앱과 SHA-256이 동일했다. 최신 로컬 소스를 확장 기준으로 선택했다.
- Drive 11/13 대비 Python 49개를 비교했다. 변경된 기존 파일은 `depth_route.py`, `map_workspace.py`, `remote_depth_route.py`, `server.py`, `jetson/local_route_host.py`, `jetson/tray_monitor.py`, `jetson/tray_protocol.py`이다. 실험 저장소·조건·예측·테스트 파일은 후속 추가되었다. 전체 결과: `source_comparison.json`, `package10_hashes.json`.
- Git은 커밋 없는 main과 사용자 미추적 파일만 있었다. 사용자 파일을 일괄 add하지 않고 `codex/d420-experiment-lab` 브랜치에서 신규 폴더만 작업한다. 변경 전 로컬 앱 ZIP: 상위 `.d420_backup/local_app_before.zip`. 원본 파일별 해시: `original_source_manifest.json`.

## 발견한 구현과 결손

| 항목 | 기존 구현 | 이번 확장 |
|---|---|---|
| 웹/지도/Nav2/L자/원격주행 | Flask가 아닌 aiohttp `server.py`, `map_workspace.py`, `navigation.py`, `remote_depth_route.py` | 원본 보존, 격리한 `legacy_app`에 신규 페이지 진입 경로만 추가 |
| 단일 모터 소유자 | `jetson/serial_bridge.py`, CH340 115200, `/d420/manual_cmd_vel`, `/odom` | USB를 추가로 열지 않는 `ExperimentAdapter`, 기존 브리지의 소유권/출력 로그 hook |
| 기존 주행 | `LocalRouteAdapter`가 L자 상태머신과 기록을 브리지 내 실행 | 신규 직선 거리적합 Profile/Controller, 기존 L자와 동시 실행 거절 |
| 웹 끊김 | 기존 L자 미션은 웹 종료 뒤에도 계속하도록 설계 | 신규 실험은 Jetson에서 0.5초 heartbeat watchdog으로 중단 |
| 수동 조종 | `operator_control.Teleop`의 0.30초 dead-man, sequence/control ID 검사 | 기존 화면을 연결하여 재사용. 신규 자동제어와 충돌 시 정지·재ARM 필요 |
| 저장 | `experiment_store.py` SQLite 비동기 기록, 실험 CSV | Windows 영상/프레임 타임스탬프/동기 CSV/지표/그래프/HTML/SHA-256/SQLite/복구/Drive 큐 |
| 트레이 | PROTO=2, `h` LEVEL, PWM 유지 모드, `@T,roll,pitch` 명령, `route_target` 소유권 | 프로토콜 검증 재사용. 예측 목표 한도·각속도·lead/ramp/filter 설정, 역방향 복구 중 예측 보상 중지 |
| 카메라 | 기존 D435는 Jetson RGB 모니터링 | 별도 Windows C920e DSHOW/MSMF 프로세스, 합성/파일 replay, 액면/앵커 독립 드래그 |
| 실험 행렬 | 기존 15조건·L자 실험 | 36조건 × 3회=108, 검증 대기/매회 ARM/수동 시작/중복 완료 방지 |
| 분석/추천 | 신규 요구 전 범위 미구현 | forward-only 품질 필터/KPI/동일조건 통계/보수 그리드 추천. 실차 반복 근거 없으면 추천 거절 |

## 적용 파일과 개발 단계

1. `lab/core.py`: 검증된 한도, 실험 설정, 7구간 jerk 프로파일, 독립 안전 상태머신, 원점 복구.
2. `lab/vision.py`, `lab/camera.py`: Windows 캡처 격리, 원본/오버레이, 추적 신뢰도, ROI, 프레임 시계.
3. `lab/jetson_adapter.py`, `jetson_experiment_bridge.py`: 기존 모터 소유자 안의 제어. `lab/ros_collector.py`, `lab/agent.py`: ROS 런타임 탐색/rosbag2/원시 로그/loopback API.
4. `lab/backend.py`, `lab/runtime.py`, `lab/sync.py`: mock/실연결 계약, 기록 ACK 후 단 한 번 START, 동기화, GUI heartbeat와 Jetson watchdog.
5. `lab/storage.py`, `lab/analysis.py`, `lab/drive.py`: 파일·DB·해시·복구, 전진 측정/통계/추천, OAuth 키링·검증 업로드·재시도.
6. `lab/server.py`, `web/`: 한국어 녹색/크림 대시보드, 제어/기록/안전정지/큐/결과/재생/API.
7. `tests/`, `scripts/ui_test.py`: 모의 단위/통합/실브라우저 검증 후에 별도 현장 commissioning.

## 안전 경계

- 기본 실행은 mock이며 실제 주소로 연결하거나 모터를 구동하지 않는다.
- 실차 확장은 설치만으로 활성화되지 않는다. 기존 시리얼 소유자를 확인·중지한 현장에서만 교체 entry point를 사용한다. 동시에 두 브리지를 실행하지 않는다.
- 실차 기본 기록 한도 .12m/s/.60m, S-curve 미검증, 트레이 OFF만, 후진 미검증. 새 제어기의 현장 검증을 대신하는 승인 기록은 아니다. 별도 프로세스 opt-in과 현장 체크/30초 단회 ARM 모두 필요하다.
- 감속·출렁임 제어보다 STOP, 외부 정지, 데이터/센서/명령 지연이 우선이다. 장애 해소 후 자동 재출발하지 않는다.
- 브라우저/Windows가 없어져도 Jetson이 heartbeat를 감시한다. 반면 브리지 프로세스 정지·전원 장애에서 물리 제동까지 보장하는 안전등급 시스템은 아니므로 현장 물리 STOP 검증이 필수다.

실제 실행 결과와 미검증 범위는 `TEST_RESULTS.md`, `REQUIREMENTS_TRACEABILITY.md`를 참조한다.

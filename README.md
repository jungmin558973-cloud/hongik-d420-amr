# HONGIK D420 AMR · L자 급정지 시험

Windows 연결 앱 + Jetson ROS 주행·트레이 제어 소스입니다.

## 최신 배포 · 2026-11-16

- [Windows 전체 설치 ZIP](https://drive.google.com/file/d/1oRYQMtdVdTiAZEqL3JoThJpGv1CZVTVF/view?usp=drivesdk)
- [설치·적용 안내](https://drive.google.com/file/d/1jnd68GsMGBcK3p3XjhlEC25S9u7kAB9k/view?usp=drivesdk)
- [기존 프로젝트 Drive 폴더](https://drive.google.com/drive/folders/1cjH8Q4XZI-BAKNUK3quQK2gk_RwC477n)

기본 경로: 1.6m 직진 → 급정지 → 실제 정지 1초 확인 → 나머지 1.6m 직진 → 정지 확인 → 오른쪽 90° 회전 → 2.2m 직진 → 도착 정지 확인.
두 직선 거리는 변경할 수 있고 급정지는 첫 직선의 절반에서 한 번 실행합니다. 기본 12cm/s, L자 시험 상한 20cm/s입니다.

## 구성

- `scenario_lab/`: Jetson 앱, 경로 엔진, 로컬 ROS 어댑터, 웹 UI, 트레이 펌웨어와 테스트.
- `windows/`: Windows 연결·설치·Jetson 배포 스크립트와 사용 안내.
- [이번 변경·검증 범위](scenario_lab/L_TEST_20261116.md).

실행은 위 전체 ZIP을 사용하세요. GitHub에는 편집 가능한 자체 소스를 보관하며, 번들 외부 라이브러리·CAD/영상 자산·컴파일 산출물은 전체 ZIP에 포함합니다. GitHub 소스만 내려받은 폴더로 Setup-Jetson.cmd를 실행하지 마세요. 설치에 필요한 hongik-team-app.tar.gz는 전체 ZIP에 있습니다.

실제 Jetson은 기존 ROS Humble/D420 장치 환경이 필요합니다. Windows는 SSH 터널과 브라우저를 사용합니다. 지도 기반 Nav2와 상대 오도메트리 L자 시험은 별도 기능입니다.

## 검증

```text
python -m unittest discover -s scenario_lab/tests -v
```

급정지·관성 있는 모의 제동·재출발·완주·관측 단절/복구·장애물·정지 실패·오도메트리 오류·호스트 제한 등 11개 모의 시험 통과.
Edge의 로컬 HTTP fixture에서 기본 속도와 급정지 선택값 전달 검증 1개 통과. Python/JavaScript 문법 검사 통과.
브라우저 테스트는 Playwright와 Edge가 필요하며 실제 센서에 연결하지 않습니다.

**이번 수정은 실제 로봇에 배포하거나 실물 주행 시험을 수행하지 않았습니다.** 센서·장애물 정지 조건을 유지합니다. 카메라 사각과 제동 성능 때문에 무조건 완주를 보장하지 않습니다. 0 속도 명령은 기계식 비상 브레이크가 아닙니다.

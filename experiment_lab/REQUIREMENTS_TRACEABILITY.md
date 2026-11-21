# 요구사항 반영표

| 요구사항 | 적용 파일·검증 상태 |
|---|---|
| 0–1 실제 소스/Drive/백업/기존 기능 보존 | `RECON_REPORT.md`, `.d420_backup`, 원본 해시, `legacy_app`. Drive 실제 다운로드/최신 ZIP SHA 비교 완료 |
| 2 한국어 통합 UI·상태·기록·비교·수동 | `web/`, `server.py`, 기존 navigation 링크·dead-man 재사용. E2E 통과. 실차 ARM 비활성 |
| 3 자동 시작/기록 ACK/프리롤/거리/정지/안정화/복구/STOP | `core.py`, `runtime.py`, `jetson_adapter.py`. mock 왕복/오류 통과. 실차 주행 미검증 |
| 3 jerk 제한/거리 적합 | `Profile` 7구간 해석 적분, 거리 부족 시 peak 이분 탐색, OFF 사다리꼴. 단위 검사 통과. 실제 모터 jerk 보장 아님 |
| 3 트레이 OFF/LEVEL/PREDICTIVE | 기존 protocol/check/route_target ACK 재사용, bounded 모델 목표. 별도 commissioning 필요, 실제 활성화하지 않음 |
| 4 36조건·108회·A~F·랜덤·매회 확인·중복 금지 | `matrix`, trial_queue SQLite, ARM/queue/start. API/E2E 통과 |
| 5 실제 신호 raw/미지원 구분 | `ros_collector.py`, `agent.py`, motor_output hook. 실제 ROS bag/odom/scan/TF 확인. ESP32 실제 연결값 미확인 |
| 6 고정 Windows 카메라·원본/오버레이·ROI/앵커·invalid | `camera.py`, `vision.py`, `devices.py`. C920 native live 확인/모의 추적 검사. 실제 액면/조명/굴절/보정 검증 필요 |
| 6 재탐색/분석 과부하 | 제한 영역 템플릿 재탐색, 손실 시 invalid/수동 재앵커. 자동 scale 보정/장시간 과부하의 사후 정밀 배치 분석은 미구현 |
| 7 원시 시계·오프셋·불확도·30Hz CSV | `sync.py`, camera_frames, clock.json. mock 수식과 실제 clock probe 확인. 광학 마커/물리 지연 검증 필요 |
| 8 close/fsync/hash/DB/Drive queue | `storage.py`, `drive.py`. 파일·DB·해시/손상 복구/실패 재시도 통과. 실제 Google OAuth/폴더/성공 업로드 미확인 |
| 9 전진 분석·비교·추천 | `analysis.py` 품질/동일 조건/반복 근거/보수 격자/승인만 초안 반영. 실제 데이터 부족 상태 반환. BO/RL은 미래 확장 |
| 10 UI/로컬 보안/에러 | loopback/토큰/동일 origin, 서버 상태 게이트. 데스크톱 브라우저 검사 통과. 모바일 원격 노출 기능은 제공하지 않음 |
| 11 실행기·문서·검사·예제·실물 구분 | CMD/Jetson scripts/README/안전/매핑/검사 JSON·JUnit/실제 예시 run·mock 영상. 실차 검증 체크리스트 별도 |

사용자 후속 지시를 우선하여 **후진은 복구만 수행하고 실험 출렁임 통계/비교/추천에 넣지 않는다.** 복귀 안전 감사 이벤트만 별도 `_recovery`에 둔다.

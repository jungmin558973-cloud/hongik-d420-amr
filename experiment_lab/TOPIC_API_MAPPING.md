# 실제 인터페이스 매핑

실제 코드는 `legacy_app/ros_io.py`, `jetson/local_route_host.py`, `jetson/serial_bridge.py`, `jetson/tray_protocol.py`, `jetson/depth_observer.py`에서 확인했다. 런타임 존재 여부는 Jetson topic inventory가 최종 근거다. 아래 기존 토픽이 모두 현재 하드웨어에서 송신 중이라고 주장하지 않는다.

| 인터페이스 | 타입/경로 | 출처·의미 |
|---|---|---|
| 차체 요청 | `/d420/manual_cmd_vel`, geometry_msgs/Twist | 기존 RosRobot → 기존 단일 serial_bridge. 신규 자동 실험은 여기서 경쟁 발행하지 않음 |
| 오도메트리 | `/odom`, nav_msgs/Odometry | 기존 브리지의 속도/자세 피드백을 적분. 미끄러짐/공분산은 실측 검증 필요 |
| LiDAR | `/scan`, sensor_msgs/LaserScan | 실제 frame TF를 확인한 전후방 안전 감시. 유효 유한 빔 부족/TF 실패는 통과 처리하지 않음 |
| D435 관측 | `/d420/depth_route/observation`, std_msgs/String JSON | ready/source_unix/points. 전방 깊이만으로 후방을 인증하지 않음 |
| 기존 L자 | `/d420/local_route/request`, `/d420/local_route/status`, String JSON | request_id/route_id/bridge_session/created_unix/ACK, 원본 기능 보존 |
| ESP32 상태 | `/d420/tray/status`, String JSON | protocol, mode, imu_fresh, roll_deg, pitch_deg, roll_pwm, pitch_pwm, zero_saved, direction_checked 등 실제 수신값 |
| 트레이 명령 | `/d420/tray/request`, String JSON | 기존 앱이 명시적으로 운영. 신규 앱은 자동 서보 전원/영점 명령을 보내지 않음 |
| 예측 소유권 | `/d420/tray/route_target`, String JSON | 기존 op/route_id/seq/issued_at/command, `@T,roll,pitch\n` 및 ACK 재사용 |
| 원본 `/cmd_vel` 등 | 실제 런타임에 발견된 이름·타입 | 존재하면 CDR 원시 bag에 수집. `/cmd_vel`이 존재한다고 가정하지 않음 |
| 전류/배터리/물리 브레이크/서보 실측각 | 미제공 | null/unsupported. servo PWM 명령과 BNO08X 트레이 각은 서로 별개 |

## 이번에 새로 정의한 확장 토픽

- `/d420/experiment/request` String JSON: op=arm/start/beat/stop/release, request_id, utc. start는 run_id/config/arm_token, beat는 run_id/seq. 기존 API로 가장하지 않는다.
- `/d420/experiment/status` String JSON: state, checks, run_id, origin 기준 이벤트, reply, validation, requested/final command, 트레이. 브리지 내부에서 발행한다.
- `/d420/experiment/motor_output` String JSON: 기존 모터 패킷 작성·write 후 기록한 UTC/monotonic/요청 속도/프로토콜 양자화 속도/packet_hex/owner/live. **패킷 전송은 실제 바퀴 응답이나 물리 브레이크 확인이 아니다.**

## Jetson loopback HTTP

모든 요청은 Bearer token과 SSH loopback 터널 필요. 기본 18866은 신규 설정값이며 기존 connection.json의 원격주행 포트와 별개다.

| 메서드/경로 | 동작 |
|---|---|
| GET `/clock` | receive/send UTC와 원시 monotonic |
| GET `/status` | 확장 상태, 실제 토픽 inventory, 원시 기록 오류, odom age |
| POST `/record/start` `{run_id}` | rosbag2 Writer/원시 logger 준비 ACK. 중복 ID 거절 |
| POST `/control/arm` `{checks}` | Jetson 단회 ARM. 프로세스 opt-in 없으면 거절 |
| POST `/control/start` `{config,run_id,arm_token}` | 원시 logger ACK·검증된 한도·센서·모터 소유권 검사 후 단 한 번 시작 |
| POST `/control/beat` `{run_id,seq}` | 단조 sequence watchdog 갱신 |
| POST `/control/stop` | 모든 프로파일보다 우선하는 정지/중단, 기존 L자도 중단 |
| POST `/control/release` | 실험 종료·차체 정지·유효 odom 확인 후 중립을 설정하고 기존 수동 제어에 소유권 반환 |
| POST `/record/stop` | 원시 Writer close/파일 확정 |
| GET `/archive/<run_id>` | 닫힌 raw evidence ZIP. 다운로드 실패해도 Jetson 원본 보존 |

Windows API는 `lab/server.py`에 정의되어 있다. 모든 변경 요청에 X-Lab-Token, 동일 origin, loopback 제한을 적용하며 거리/속도/모드/상태는 서버에서도 검사한다. 원래 네비게이션 URL은 배포 `connection.json`의 local_port로 만든다.

rosbag2 CDR은 원시 데이터이며 JSONL 수신 로그는 보조 정보다. discovery/subscriber 한계와 디스크 처리량에 따라 DDS 손실이 발생할 수 있으므로 무손실을 주장하지 않는다. 기록 큐 초과를 감지하면 오류/중단한다. Humble의 QoS 호환성은 [공식 문서](https://docs.ros.org/en/humble/How-To-Guides/Overriding-QoS-Policies-For-Recording-And-Playback.html), Writer는 [rosbag2 원본](https://github.com/ros2/rosbag2/tree/humble)을 기준으로 구현했다. 카메라 backend는 [OpenCV 공식 enum](https://docs.opencv.org/doc/doxygen/html/d4/d15/group__videoio__flags__base.html)을 확인했다.

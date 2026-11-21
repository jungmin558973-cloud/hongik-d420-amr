"""A self-contained Korean explanation beside every recorded experiment."""
import json


LABELS={
 'distance':'정지 명령을 내릴 주행 거리 (m)','speed':'목표 속도 (m/s; 0.2=20cm/s)',
 's_curve':'S-curve 사용','tray':'트레이 모드 (OFF/LEVEL/PREDICTIVE)',
 's_curve_strength':'S-curve 강도 (0=OFF, 100=설정 Jerk; 중간값의 적용 Jerk=설정 Jerk×100/강도; 거리 급정지에는 적용하지 않음)',
 'acceleration':'설정 가속도 (m/s²)','deceleration':'설정 감속도 (m/s²; 급정지는 영속도 명령)',
 'jerk':'설정 Jerk (m/s³)','pre_roll':'주행 전 녹화 (초)','settle':'정지 안정화 관찰 (초)',
 'post_roll':'후속 녹화 (초; settle과 합쳐 정지 후 총 관찰 시간)',
 'return_home':'측정 후 후진 복구','repeats':'조건별 계획 촬영 횟수 (자동 연속 출발 아님)',
 'fill_pct':'설정 수위 (%)','cup':'용기 종류','diameter_mm':'설정 용기 직경 (mm)',
 'water_g':'설정 액체 질량 (g)','lid':'뚜껑 사용','camera_setting':'카메라/ROI 설정 식별자',
 'scenario':'실험 시나리오','notes':'사용자 메모','lead':'트레이 예측 선행 시간 (초)',
 'gain':'트레이 예측 이득','angle_limit':'트레이 명령 각도 상한 (도)',
 'angle_rate':'트레이 명령 각속도 상한 (도/초)','ramp':'트레이 명령 램프 시간 (초)',
 'filter_s':'트레이 명령 필터 시간 (초)','center_deg':'트레이 명령 중심 오프셋 (도)',
 'brake_offset_m':'설정 제동 오프셋 (m)','min_cruise_s':'정속 확인 기준 시간 (초)',
 'require_4k':'4K 필수 여부','record_priority':'원본 녹화 우선/실시간 분석 유예',
 'stop_semantics':'brake_at_distance: 해당 거리에서 급정지 명령, 실제 정지 위치는 별도',
 'commissioning':'사용자가 명시적으로 요청한 촬영 (하드웨어 검증 완료를 의미하지 않음)',
 'obstacle_stop':'장애물 판정 자동정지 (false: 센서값 기록만 수행)'}


def write_notes(path,config,label,metrics=None):
    m=metrics or {};outcome=m.get('completion',{})
    total=config.get('settle',0)+config.get('post_roll',0)
    lines=['# 실험 설명서','',f"- 실험 ID: `{label['run_id']}`",f"- 저장 이름: `{label['display_name']}`",
           f"- 설정 거리: **{config.get('distance')} m**",f"- 설정 속도: **{config.get('speed',0)*100:g} cm/s**",
           f'- 정지 후 계획 녹화: **{total:g}초**','',
           '## 이번 촬영의 순서','',
           '버튼 시작 → 영상·ROS 원시 로그 기록 → 전진 → 설정 거리에서 급정지 명령 → 실제 정지 확인 → 10초 관찰 → 녹화 종료·저장.',
           '설정 거리는 급정지 명령 위치입니다. 관성 때문에 실제 최종 정지 위치는 더 멀 수 있으며 아래 결과에 별도로 기록합니다.','',
           '## 결과','']
    if outcome:
        lines += [f"- 완료 판정: **{outcome.get('folder_status')}**",f"- 목표 거리까지 주행 확인: {outcome.get('motion_success')}",
                  f"- 실제 정지 거리: {outcome.get('actual_stop_distance_m','확인 불가')} m",
                  f"- 정지 후 실제 영상 길이: {outcome.get('post_stop_video_s','확인 불가')} 초",
                  '- 실패/미확인 사유: '+(' / '.join(outcome.get('reasons',[])) or '없음')]
    else:lines += ['촬영 중입니다. 종료 시 실제 거리·10초 녹화·성공 여부로 갱신됩니다.']
    lines += ['', '주행·녹화 성공 여부와 출렁임 분석 품질/IMU 수신 여부는 별도입니다. 없는 데이터는 0으로 만들지 않습니다.','',
              '## 설정값 하나씩','', '| 설정 키 | 의미 | 저장한 값 |','|---|---|---|']
    for key,value in config.items():
        shown=json.dumps(value,ensure_ascii=False).replace('|','\\|').replace('\n',' / ')
        lines.append(f'| `{key}` | {LABELS.get(key,key)} | {shown} |')
    lines += ['', '트레이 예측 관련 값은 OFF 모드에서는 적용되지 않은 설정값입니다. 설정 목표 속도와 실제 속도는 다를 수 있습니다.','',
              '## 파일 하나씩','',
              '- `webcam_raw.avi`: C920 원본 해상도의 실험 영상. 화면 선/글자를 덧그리지 않은 분석 입력입니다.',
              '- `camera_frames.jsonl`, `camera_frames.csv`: 프레임 번호, 실제 수신 UTC·단조 시각, 해상도와 프레임 간격입니다. 영상의 명목 FPS보다 이 시각을 우선합니다.',
              '- `video_capture.json`, `camera_device.json`: 실제 파일/코덱 및 선택 카메라 설정입니다.',
              '- `robot_raw.jsonl`: 접근 가능한 실제 ROS 메시지와 수신 시각입니다.',
              '- `robot_telemetry.jsonl`: 실제 속도·오도메트리·명령·주행 상태입니다.',
              '- `rosbag2/`: 수집한 ROS 메시지의 원시 CDR 로그입니다.',
              '- `imu.csv`, `imu_availability.json`: 실제 IMU/트레이 자세와 유효 샘플 수입니다. 헤더만 있는 CSV는 IMU 미수신입니다.',
              '- `events.jsonl`: 녹화·전진·제동·정지·종료 또는 중단 이벤트입니다.',
              '- `clock.json`: Jetson 시각과 Windows 영상 시각의 차이 및 동기화 불확실성입니다.',
              '- `synchronized.csv`: 동일 시간축에 맞춘 영상 프레임과 주행 데이터입니다. 누락 구간은 임의 보간하지 않습니다.',
              '- `completion.json`: 목표 거리 도달, 정지 후 영상 길이, 성공/실패와 사유입니다.',
              '- `protocol_validation.json`: 정속 구간·자동 정지·영상·IMU 등 연구 조건의 품질 판정입니다.',
              '- `metrics.json`: 측정 가능한 주행/출렁임 지표입니다. 사후 분석 전에는 출렁임 값이 없습니다.',
              '- `plots/`, `report.html`: 그래프와 보고서입니다. 자료가 없으면 N/A로 표시합니다.',
              '- `config.json`, `run_label.json`: 이 실험에 실제 요청한 설정과 식별 정보입니다.',
              '- `manifest.json`: 파일 크기·SHA-256 해시 및 종료 상태입니다.',
              '- `capture_exclusions.json`: 단일 제어 소유권 보호와 영상 중복 저장 방지를 위해 제외한 ROS 항목입니다.',
              '- `camera_analysis.jsonl`, `webcam_annotated.mp4`: 사후 분석을 실행했을 때 생성되는 점 추적 결과와 표시 영상입니다.',
              '- `peak_frame.png`, `peak_event.json`: 검출한 최대 액면 상승 순간과 높이입니다. 보정 전 단위는 px이며 mm가 아닙니다.',
              '- `peak_imu_window.csv`, `peak_robot_window.csv`, `peak_surface_points.csv`: 최대 순간 전후의 IMU·주행·표면 검출점입니다.','',
              '존재하지 않는 파일은 해당 센서가 없었거나 아직 사후 분석을 실행하지 않았다는 뜻일 수 있습니다. ZIP은 이 폴더의 추가 보관용 사본이며 폴더별 원본을 보존합니다.','']
    (path/'실험설명서.md').write_text('\n'.join(lines),encoding='utf-8')

L자 시험 업데이트: README_L_TEST.txt를 먼저 읽으세요.

HONGIK CAFE — Windows 연결 앱 / 2026-11-15

[처음 한 번: Windows PC]
1. ZIP을 Windows PC로 옮긴 뒤 반드시 '모두 압축 풀기'를 합니다.
2. Install-HongikCafe.cmd를 더블클릭합니다.
3. 바탕화면에 HONGIK CAFE 바로가기가 생깁니다.
   설치는 사용자 폴더에만 하며 Windows 관리자 권한은 요구하지 않습니다.
   설치 없이 쓸 때는 Open-HongikCafe.cmd를 더블클릭해도 됩니다.

[매번 사용할 때]
1. 로봇의 Jetson과 차체 전원을 켭니다. 차체 USB는 Jetson에 연결합니다.
2. Windows PC를 같은 K221 Wi-Fi에 연결합니다.
3. 바탕화면 HONGIK CAFE를 누릅니다.
4. 별도 SSH 창에서 seo 계정 암호를 입력합니다. 키 인증이 설정된 PC는 생략됩니다.
   암호 입력 중 글자가 표시되지 않는 것은 정상입니다. 암호를 파일에 저장하지 않습니다.
5. 앱이 연결되면 브라우저에 현재 HONGIK CAFE 화면이 열립니다.
6. 실차 수동 조종 화면이 선택되어 열립니다. R 또는 '수동 조종 켜기'를 누릅니다.
   WASD/방향키: 전후진/회전, W+A/D: 이동하면서 회전, A/D: 제자리 회전.
   Space/Esc: 정지. 키를 놓으면 해당 축이 정지합니다.
7. 종료할 때 앱에서 정지한 뒤 연결 창의 '접속 종료'를 누릅니다.
   연결 창을 닫을 때도 정지 요청을 먼저 보냅니다. 전달 실패 시 알림을 표시합니다.

[연결 창]
- Jetson 주소 입력 후 '연결하기': 주소를 이 PC에 저장합니다.
- '주행·트레이 앱 열기': 기존 앱 전체 화면을 엽니다.
- '지도·저장 지점 열기': RViz/맵/저장 지점 화면을 엽니다.
- '앱 정지 요청': 연결된 앱에 정지 요청을 보냅니다. 차체 상태는 현장에서 확인합니다.
- 재연결: 연결 이후 SSH가 끊어지면 다시 연결합니다. 주행을 켜는 명령은 보내지 않습니다.
- 초기 부팅/암호 입력 대기는 최대 약 3분입니다. 실패하면 주소·Wi-Fi·SSH 창을 확인한 뒤 다시 연결합니다.
- 연결 창과 SSH 창은 사용하는 동안 열어 두세요.

[Jetson 담당자만: 서버 업데이트/자동 부팅 재설치]
2026-11-15 SJM에 최신 앱과 자동 시작 설정을 적용했습니다.
전원 ON 후 서비스 자동 실행, SSH 터널을 통한 실제 카메라·지도·RViz, 정지 상태를 확인했습니다.
일반 팀원은 위 Windows 설치만 하면 됩니다. 실제 Windows PC에서의 실행 확인은 남아 있습니다.
새 앱 업로드/서비스 재설치가 필요하면 담당자가 다음을 한 번 수행합니다.
1. Jetson을 켜고 차체를 앱에서 정지시킵니다.
2. 연결 창은 종료한 뒤 Setup-Jetson.cmd를 실행합니다.
3. seo 암호로 인증하면 동봉한 최신 앱을 전송하고 기존 코드를 백업합니다.
4. 자동 시작 서비스 3개와 사용자 Linger를 설정합니다.
   Linger 권한 오류만 나오면 Jetson에서 sudo loginctl enable-linger seo 를 한 번 실행한 뒤 재시도합니다.
5. 지도·모델·실험 데이터는 덮어쓰지 않습니다. ROS Humble 및 기존 D420 장치 환경이 있는 SJM 대상입니다.
6. 담당자가 서버를 재설치한 뒤에는 센서 준비와 앱 접속을 확인합니다. 접속만으로 조종은 켜지지 않습니다.

[필요한 것]
Windows 10/11의 Windows PowerShell 5.1, OpenSSH 클라이언트, Edge 또는 Chrome.
리눅스·WSL·ROS·Python·별도 웹 서버를 Windows에 설치하지 않습니다.
OpenSSH가 없으면 Windows '선택적 기능'에서 'OpenSSH 클라이언트'를 설치하세요.
OpenSSH 서버는 Windows PC에 필요하지 않습니다. 선택적 기능 설치는 관리자 승인이 필요할 수 있습니다.
공식 안내: https://learn.microsoft.com/en-us/windows-server/administration/openssh/openssh_install_firstuse

[주소와 저장 위치]
기본 Jetson: seo@192.168.0.47 (K221 내부 주소, 변경 가능)
Windows 앱: http://127.0.0.1:18765/ / 지도: http://127.0.0.1:18765/navigation
설치 앱: %LOCALAPPDATA%\HongikCafe\app
PC별 주소: %LOCALAPPDATA%\HongikCafe\connection.json
연결 기록: %LOCALAPPDATA%\HongikCafe\launcher.log (암호·세션 토큰 제외)
로봇 서버: 127.0.0.1:8765, ROS/USB/센서 처리는 Jetson에서 실행합니다.

[연결 문제]
- No route / timed out: Jetson 전원, 같은 K221인지, 현재 IP인지 확인합니다.
- Permission denied: seo 암호/인증을 확인합니다.
- Host key mismatch: 담당자가 Jetson 공개 키를 확인해야 합니다. 키 검사를 끄지 않습니다.
- 포트 사용 중: 이전 접속 창/터널을 종료합니다. 이 실행기는 PC당 한 개만 엽니다.
- 앱은 열리나 센서가 준비 안 됨: 차체 전원·USB와 Jetson 서비스 상태를 확인합니다.
  Jetson에서 systemctl --user status hongik-scenarios hongik-depth-observer hongik-team-ui
- 연결 창은 앱 연결 여부만 판단합니다. 센서/차체 준비 상태는 앱에서 별도로 확인합니다.

한 번에 한 명이 조종합니다. 기존 ASUS 구동 앱과 동시에 조종하지 않습니다.
수동 주행은 앱의 연결 단절 정지를 유지합니다. Jetson 내부 자율 경로는 노트북 통신이
끊겨도 로컬 센서 조건에 따라 계속될 수 있습니다. 접속 복구는 새 주행 명령을 보내지 않습니다.
이 배포는 인터넷 밖에서 접속하는 설정이 아니라 같은 로컬 네트워크에서의 원격 조종입니다.
실행/접속/부팅만으로 주행을 시작하지 않습니다. 속도와 운전 로직은 기존 앱 설정을 사용합니다.

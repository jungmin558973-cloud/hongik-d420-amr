#!/usr/bin/env bash
# Build only: never opens a serial port or uploads to a board.
set -euo pipefail
firmware_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
tool_root="${HONGIK_ARDUINO_ROOT:-/tmp/hongik-arduino-build}"
mkdir -p "$tool_root" "$firmware_root/compiled_esp32s3"
if [[ ! -x "$tool_root/arduino-cli" ]]; then
    python3 - "$tool_root" <<'PY'
from pathlib import Path
import sys,tarfile,urllib.request,shutil
root=Path(sys.argv[1]);archive=root/'arduino-cli.tar.gz'
urllib.request.urlretrieve('https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_64bit.tar.gz',archive)
with tarfile.open(archive) as tar:
    member=tar.getmember('arduino-cli')
    with tar.extractfile(member) as source,(root/'arduino-cli').open('wb') as target:shutil.copyfileobj(source,target)
(root/'arduino-cli').chmod(0o755)
PY
fi
python3 - "$tool_root" <<'PY'
import sys,json
from pathlib import Path
p=Path(sys.argv[1])
config={'board_manager':{'additional_urls':['https://espressif.github.io/arduino-esp32/package_esp32_index.json']},
        'directories':{'data':str(p/'data'),'downloads':str(p/'downloads'),'user':str(p/'user')},
        'updater':{'enable_notification':False}}
(p/'arduino-cli.yaml').write_text(json.dumps(config))
PY
cli=("$tool_root/arduino-cli" --config-file "$tool_root/arduino-cli.yaml")
"${cli[@]}" version
"${cli[@]}" core update-index
"${cli[@]}" core install esp32:esp32@3.3.0
"${cli[@]}" lib install 'ESP32Servo@3.0.9' 'Adafruit BNO055@1.6.4'
"${cli[@]}" compile --fqbn esp32:esp32:esp32s3:CDCOnBoot=cdc --jobs 2 \
    --build-path "$tool_root/build" --output-dir "$firmware_root/compiled_esp32s3" \
    "$firmware_root/esp32_app_control"
"${cli[@]}" core list --format json > "$firmware_root/compiled_esp32s3/core_versions.json"
"${cli[@]}" lib list --format json > "$firmware_root/compiled_esp32s3/library_versions.json"

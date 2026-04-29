#!/bin/bash
# 라즈베리파이 부팅 시 자동 실행 설정
# 실행: bash autostart.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="/etc/systemd/system/heyfood-counter.service"

echo "자동 실행 서비스 등록 중..."

sudo bash -c "cat > $SERVICE_FILE" << EOF
[Unit]
Description=헤이푸드 유부초밥 생산 카운터
After=network.target

[Service]
ExecStart=/usr/bin/python3 $SCRIPT_DIR/counter.py
WorkingDirectory=$SCRIPT_DIR
Restart=always
User=pi
Environment=DISPLAY=:0

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable heyfood-counter
sudo systemctl start heyfood-counter

echo ""
echo "✅ 자동 실행 등록 완료!"
echo "상태 확인: sudo systemctl status heyfood-counter"
echo "로그 확인: sudo journalctl -u heyfood-counter -f"

#!/bin/bash
# ─────────────────────────────────────────────
# 헤이푸드 통합 관제 시스템 - 자동 실행 등록
# ─────────────────────────────────────────────
# 사용법:
#   bash autostart.sh yooboo        → 유부초밥 성형기 (counter.py)
#   bash autostart.sh filling       → 자동충진 실링기 (counter2.py)
#   bash autostart.sh temp          → 1층 온습도 (temperature.py)
#   bash autostart.sh metal         → 금속검출기 (metal_detector.py)
#   bash autostart.sh all-2f        → 2층 라즈베리 (yooboo + filling 동시)
# ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-yooboo}"

# 장비 → 파이썬 파일 매핑
case "$TARGET" in
  yooboo)
    SCRIPTS=("counter.py")
    SERVICES=("heyfood-yooboo")
    ;;
  filling)
    SCRIPTS=("counter2.py")
    SERVICES=("heyfood-filling")
    ;;
  temp)
    SCRIPTS=("temperature.py")
    SERVICES=("heyfood-temp")
    ;;
  metal)
    SCRIPTS=("metal_detector.py")
    SERVICES=("heyfood-metal")
    ;;
  all-2f)
    SCRIPTS=("counter.py" "counter2.py")
    SERVICES=("heyfood-yooboo" "heyfood-filling")
    ;;
  *)
    echo "사용법: bash autostart.sh [yooboo|filling|temp|metal|all-2f]"
    exit 1
    ;;
esac

for i in "${!SCRIPTS[@]}"; do
  SCRIPT="${SCRIPTS[$i]}"
  SERVICE="${SERVICES[$i]}"
  SERVICE_FILE="/etc/systemd/system/${SERVICE}.service"
  
  echo ""
  echo "▶ ${SERVICE} 등록 중 (${SCRIPT})..."
  
  sudo bash -c "cat > $SERVICE_FILE" << EOF
[Unit]
Description=헤이푸드 - ${SERVICE} (${SCRIPT})
After=network.target

[Service]
ExecStart=/usr/bin/python3 $SCRIPT_DIR/$SCRIPT
WorkingDirectory=$SCRIPT_DIR
Restart=always
RestartSec=5
User=pi
Environment=DISPLAY=:0

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE"
  sudo systemctl restart "$SERVICE"
  
  echo "   ✅ ${SERVICE} 등록 + 시작 완료"
done

echo ""
echo "======================================"
echo "✅ 자동 실행 등록 완료!"
echo "======================================"
echo "등록된 서비스:"
for SERVICE in "${SERVICES[@]}"; do
  echo "  - $SERVICE"
done
echo ""
echo "상태 확인:"
for SERVICE in "${SERVICES[@]}"; do
  echo "  sudo systemctl status $SERVICE"
done
echo ""
echo "로그 확인:"
for SERVICE in "${SERVICES[@]}"; do
  echo "  sudo journalctl -u $SERVICE -f"
done

#!/bin/bash
# ================================================================
# 헤이푸드 AI 불량 검출 시스템 설치 스크립트
# 라즈베리파이5 64-bit OS 전용
#
# 실행 방법:
#   chmod +x install_ai.sh
#   bash install_ai.sh
# ================================================================

set -e  # 오류 발생 시 즉시 중단

# ── 색상 출력 ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}   $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# ── 현재 디렉토리 확인 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
info "설치 경로: $SCRIPT_DIR"
cd "$SCRIPT_DIR"

# ────────────────────────────────────────────
# 1. 시스템 패키지 업데이트
# ────────────────────────────────────────────
echo ""
echo "=================================================="
echo "  1단계: 시스템 패키지 업데이트"
echo "=================================================="

info "apt 패키지 목록 업데이트 중..."
sudo apt-get update -qq

info "필수 시스템 패키지 설치 중..."
sudo apt-get install -y \
    python3-pip \
    python3-venv \
    python3-dev \
    libatlas-base-dev \
    libhdf5-dev \
    libhdf5-serial-dev \
    libjpeg-dev \
    libpng-dev \
    libtiff-dev \
    libopenblas-dev \
    liblapack-dev \
    libffi-dev \
    libssl-dev \
    cmake \
    pkg-config \
    git \
    curl \
    2>/dev/null || warn "일부 패키지 설치 실패 (계속 진행)"

ok "시스템 패키지 설치 완료"

# ────────────────────────────────────────────
# 2. Python 가상환경 생성
# ────────────────────────────────────────────
echo ""
echo "=================================================="
echo "  2단계: Python 가상환경 구성"
echo "=================================================="

VENV_DIR="$SCRIPT_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
    info "가상환경 생성 중: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
    ok "가상환경 생성 완료"
else
    ok "기존 가상환경 사용: $VENV_DIR"
fi

# 가상환경 활성화
source "$VENV_DIR/bin/activate"
info "가상환경 활성화: $(which python3)"

# pip 업그레이드
pip install --upgrade pip --quiet

# ────────────────────────────────────────────
# 3. Python 패키지 설치
# ────────────────────────────────────────────
echo ""
echo "=================================================="
echo "  3단계: Python 패키지 설치"
echo "=================================================="

# ── OpenCV (라즈베리파이 최적화 버전) ──
info "OpenCV 설치 중..."
pip install opencv-python-headless --quiet || \
    pip install opencv-python --quiet || \
    warn "OpenCV 설치 실패 - 나중에 수동으로 설치하세요"
ok "OpenCV 설치 완료"

# ── TensorFlow Lite Runtime (라즈베리파이5 64bit용) ──
info "TFLite Runtime 설치 시도 중..."
PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}{sys.version_info.minor}')")
ARCH=$(uname -m)
info "Python 버전: $PYTHON_VER | 아키텍처: $ARCH"

TFLITE_INSTALLED=false

# tflite-runtime 공식 패키지 시도
if pip install tflite-runtime --quiet 2>/dev/null; then
    ok "tflite-runtime 설치 완료"
    TFLITE_INSTALLED=true
fi

# 실패 시 tensorflow 전체 설치 (용량 크지만 안정적)
if [ "$TFLITE_INSTALLED" = false ]; then
    warn "tflite-runtime 설치 실패, TensorFlow 전체 설치 시도..."
    warn "⚠ 라즈베리파이에서 TensorFlow 설치는 수 분이 걸릴 수 있습니다..."
    if pip install tensorflow --quiet 2>/dev/null; then
        ok "TensorFlow 설치 완료"
        TFLITE_INSTALLED=true
    else
        # ARM64 nightly 버전 시도
        warn "ARM64용 tensorflow-cpu 시도..."
        pip install tensorflow-cpu --quiet 2>/dev/null && \
            ok "tensorflow-cpu 설치 완료" && TFLITE_INSTALLED=true || \
            warn "TensorFlow 설치 실패 - 더미 모드로만 동작합니다"
    fi
fi

# ── Flask + SocketIO + CORS ──
info "Flask 및 웹 서버 패키지 설치 중..."
pip install \
    flask \
    flask-socketio \
    flask-cors \
    python-socketio \
    eventlet \
    --quiet
ok "Flask 패키지 설치 완료"

# ── NumPy (TFLite 의존성) ──
info "NumPy 설치 중..."
pip install "numpy<2.0" --quiet
ok "NumPy 설치 완료"

# ── Pillow (이미지 처리) ──
pip install Pillow --quiet

# ── requirements.txt 업데이트 ──
info "requirements.txt 업데이트 중..."
pip freeze > "$SCRIPT_DIR/requirements_ai.txt"
ok "requirements_ai.txt 생성 완료"

# ────────────────────────────────────────────
# 4. 디렉토리 생성
# ────────────────────────────────────────────
echo ""
echo "=================================================="
echo "  4단계: 필요 디렉토리 생성"
echo "=================================================="

dirs=(
    "data/정상"
    "data/불량"
    "data/defects"
    "model"
)

for d in "${dirs[@]}"; do
    if mkdir -p "$SCRIPT_DIR/$d"; then
        ok "생성: $d"
    fi
done

# .gitkeep 파일 생성 (빈 폴더 git 추적용)
touch "$SCRIPT_DIR/data/정상/.gitkeep"
touch "$SCRIPT_DIR/data/불량/.gitkeep"
touch "$SCRIPT_DIR/data/defects/.gitkeep"
touch "$SCRIPT_DIR/model/.gitkeep"

# ────────────────────────────────────────────
# 5. systemd 서비스 등록
# ────────────────────────────────────────────
echo ""
echo "=================================================="
echo "  5단계: systemd 서비스 등록"
echo "=================================================="

SERVICE_NAME="ai-defect"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CURRENT_USER=$(whoami)

info "서비스 파일 생성 중: $SERVICE_FILE"
sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=헤이푸드 AI 불량 검출 시스템
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_DIR}/bin/python3 ${SCRIPT_DIR}/ai_defect.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# systemd 서비스 활성화
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"
ok "서비스 등록 완료: ${SERVICE_NAME}"
info "서비스 시작 명령: sudo systemctl start ${SERVICE_NAME}"
info "서비스 상태 확인: sudo systemctl status ${SERVICE_NAME}"
info "서비스 로그 보기: sudo journalctl -u ${SERVICE_NAME} -f"

# ────────────────────────────────────────────
# 6. 설치 완료 메시지
# ────────────────────────────────────────────
echo ""
echo "=================================================="
echo -e "  ${GREEN}✅ 설치 완료!${NC}"
echo "=================================================="
echo ""
echo "  📁 폴더 구조:"
echo "     data/정상/    ← 정상 유부초밥 사진을 여기에 넣으세요"
echo "     data/불량/    ← 불량 유부초밥 사진을 여기에 넣으세요"
echo "     data/defects/ ← 감지된 불량 이미지 자동 저장"
echo "     model/        ← 학습된 모델 파일 저장"
echo ""
echo "  🚀 다음 단계:"
echo "     1) 사진 촬영: data/정상/ 과 data/불량/ 에 각 50장 이상"
echo "     2) 모델 학습: source venv/bin/activate && python3 train.py"
echo "     3) 검출 시작: python3 ai_defect.py"
echo "     4) 대시보드:  http://$(hostname -I | awk '{print $1}'):5001"
echo ""
echo "  🔧 서비스 명령:"
echo "     시작: sudo systemctl start ai-defect"
echo "     중지: sudo systemctl stop ai-defect"
echo "     상태: sudo systemctl status ai-defect"
echo ""

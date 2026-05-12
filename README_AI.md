# 헤이푸드 AI 비전 불량 검출 시스템

라즈베리파이5 카메라로 유부초밥 생산 라인을 실시간 모니터링하여 불량품을 자동 감지하는 AI 시스템입니다.

---

## 📁 파일 구조

```
heyfood-counter/
├── train.py          # AI 모델 학습 스크립트 (MobileNetV2)
├── ai_defect.py      # 실시간 불량 검출 + 웹 대시보드 (포트 5001)
├── install_ai.sh     # 설치 스크립트 (TFLite, OpenCV, Flask 등)
├── counter.py        # 기존 생산 카운팅 대시보드 (포트 5000) ← 불량 현황 추가됨
│
├── data/
│   ├── 정상/         # 정상 유부초밥 학습 사진 (여기에 넣으세요)
│   ├── 불량/         # 불량 유부초밥 학습 사진 (여기에 넣으세요)
│   ├── defects/      # 감지된 불량 이미지 자동 저장 (YYYYMMDD_HHMMSS.jpg)
│   └── defect_log.db # SQLite 불량 이력 DB
│
└── model/
    ├── defect_model.h5      # Keras 학습 모델
    ├── defect_model.tflite  # TFLite 변환 모델 (라즈베리파이 최적화)
    └── class_indices.json   # 클래스 레이블 인덱스
```

---

## ⚙️ 설치 방법

### 1. 설치 스크립트 실행

```bash
cd /home/pi/heyfood-counter   # 또는 프로젝트 경로
chmod +x install_ai.sh
bash install_ai.sh
```

설치 내용:
- TFLite Runtime (또는 TensorFlow)
- OpenCV (카메라 처리)
- Flask + Flask-SocketIO (웹 대시보드)
- 필요 디렉토리 자동 생성
- systemd 서비스 자동 등록

### 2. 가상환경 활성화

```bash
source venv/bin/activate
```

---

## 📸 학습 데이터 준비 방법

모델 품질은 학습 사진의 수와 다양성에 달려 있습니다.

### 촬영 가이드

| 항목 | 권장 사항 |
|------|-----------|
| 수량 | 각 클래스당 최소 **50장**, 200장 이상 권장 |
| 해상도 | 640×480 이상 (카메라 기본 해상도) |
| 조명 | 실제 생산 라인과 동일한 조명 조건 |
| 각도 | 카메라 설치 위치에서 촬영 |
| 배경 | 실제 컨베이어 벨트 위에서 촬영 |

### 정상 사진 기준 (`data/정상/`)
- 모양이 균일한 유부초밥
- 크기가 표준 범위 내
- 색상이 고른 것
- 터짐/찌그러짐 없는 것

### 불량 사진 기준 (`data/불량/`)
- 터진 유부초밥
- 찌그러진 형태
- 크기 이상 (너무 크거나 작음)
- 색상 불균일 (탄 부분, 덜 익은 부분)
- 내용물이 빠져나온 것

### 사진 수집 방법

```bash
# 라즈베리파이 카메라로 사진 촬영
# 정상 사진 촬영 (타임스탬프 파일명으로 저장)
raspistill -o data/정상/normal_$(date +%Y%m%d_%H%M%S).jpg

# 또는 libcamera 사용 (라즈베리파이5)
libcamera-jpeg -o data/정상/normal_$(date +%Y%m%d_%H%M%S).jpg
```

또는 기존 생산 라인에서 수동으로 사진을 찍어 폴더에 복사합니다.

---

## 🤖 모델 학습 방법

```bash
# 가상환경 활성화
source venv/bin/activate

# 학습 실행 (약 10~30분, 라즈베리파이 성능에 따라 다름)
python3 train.py
```

### 학습 과정

1. **1단계 (Transfer Learning)**: MobileNetV2 기반 모델 동결, 분류기만 학습
2. **2단계 (Fine-tuning)**: 상위 레이어 일부 해동 후 전체 미세 조정
3. 최고 검증 정확도 기준으로 `model/defect_model.h5` 자동 저장
4. `.h5` → `.tflite` 자동 변환 (양자화 적용으로 모델 크기 ~4배 축소)

### 학습 결과 예시

```
==================================================
  학습 완료 결과
==================================================
  총 학습 에폭:       25
  최고 검증 정확도:   94.50%  (에폭 18)
  최종 학습 정확도:   97.20%
  최종 검증 정확도:   93.80%

  모델 저장 위치: model/defect_model.h5
==================================================
  H5 모델 크기:     14.2 MB
  TFLite 모델 크기: 3.8 MB (약 73% 압축)
```

**검증 정확도 85% 미만이면**: 학습 데이터를 더 추가하거나 조명/각도를 개선하세요.

---

## 🚀 불량 검출 실행 방법

### 수동 실행

```bash
source venv/bin/activate
python3 ai_defect.py
```

### 서비스로 자동 시작 (부팅 시 자동 실행)

```bash
# 서비스 시작
sudo systemctl start ai-defect

# 부팅 시 자동 시작 활성화
sudo systemctl enable ai-defect

# 상태 확인
sudo systemctl status ai-defect

# 실시간 로그
sudo journalctl -u ai-defect -f
```

### 더미 모드 (카메라 없이 테스트)

카메라가 연결되지 않으면 자동으로 더미 모드로 전환됩니다.
대시보드 상단에 `⚠ 더미모드` 배지가 표시됩니다.

---

## 🌐 대시보드 접속 방법

### AI 불량 검출 대시보드 (포트 5001)

```
http://[라즈베리파이 IP]:5001
```

IP 확인 방법:
```bash
hostname -I | awk '{print $1}'
```

### 생산 카운팅 대시보드 (포트 5000, 기존)

```
http://[라즈베리파이 IP]:5000
```

> 기존 counter.py 대시보드에 오늘 불량 현황 섹션이 추가되었습니다.

### 대시보드 기능

| 기능 | 설명 |
|------|------|
| 실시간 카메라 영상 | MJPEG 스트리밍, 판정 결과 오버레이 |
| 검사 수량 | 오늘 총 검사 수량 |
| 불량 수량 | 오늘 불량 감지 수량 |
| 불량률 (%) | 실시간 불량률 계산 |
| 경고 알림 | 불량률 5% 초과 시 빨간 배너 + 테두리 점멸 |
| 최근 불량 사진 | 최근 5건 썸네일 + 시각 + 신뢰도 |
| SocketIO 실시간 | 0.5초 간격 자동 갱신 (페이지 새로고침 불필요) |

---

## 🔧 설정 변경

`ai_defect.py` 상단의 `CONFIG` 딕셔너리를 수정합니다:

```python
CONFIG = {
    "camera_index": 0,          # 카메라 번호 변경 시
    "defect_threshold": 0.5,    # 불량 판단 임계값 (높일수록 엄격)
    "warn_defect_rate": 5.0,    # 경고 불량률 임계값 (%)
    "inference_interval": 0.5,  # 추론 간격 (초) - CPU 부하 조절
    "server_port": 5001,        # 포트 번호
}
```

---

## 🐛 문제 해결

### 카메라를 인식 못하는 경우

```bash
# 카메라 연결 확인
ls /dev/video*

# v4l2 장치 확인
v4l2-ctl --list-devices

# config.txt에서 카메라 활성화 (라즈베리파이 카메라 모듈)
sudo raspi-config
# → Interface Options → Camera → Enable
```

### 모델 파일이 없는 경우

더미 모드로 실행됩니다. `train.py`를 먼저 실행하세요.

### 포트 5001 이미 사용 중인 경우

```bash
sudo fuser -k 5001/tcp
```

### TFLite 설치 오류

```bash
# ARM64 수동 설치
pip install --extra-index-url https://google-coral.github.io/py-repo/ tflite_runtime
```

---

## 📊 DB 직접 조회

```bash
sqlite3 data/defect_log.db

# 오늘 통계
SELECT date, result_type, COUNT(*) FROM defect_log
WHERE date = date('now') GROUP BY result_type;

# 최근 불량 10건
SELECT timestamp, confidence, image_path
FROM defect_log WHERE result_type = '불량'
ORDER BY id DESC LIMIT 10;
```

---

## 📋 시스템 요구사항

- 라즈베리파이5 (64-bit OS 권장)
- Python 3.9 이상
- 카메라: USB 웹캠 또는 라즈베리파이 카메라 모듈
- 저장공간: 최소 2GB (TensorFlow 포함 시 4GB)
- RAM: 4GB 이상 권장 (학습 시 8GB 권장)

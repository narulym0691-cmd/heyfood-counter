#!/usr/bin/env python3
"""
헤이푸드 유부초밥 AI 불량 검출 시스템
OpenCV 실시간 카메라 + TFLite 모델 + Flask/SocketIO 웹 대시보드

실행: python3 ai_defect.py
대시보드: http://라즈베리파이IP:5001
"""

import os
import sys
import cv2
import time
import json
import sqlite3
import threading
import numpy as np
from datetime import datetime, date
from pathlib import Path
from flask import Flask, Response, jsonify, render_template_string
from flask_socketio import SocketIO, emit
from flask_cors import CORS

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
CONFIG = {
    "camera_index": 0,              # 카메라 번호 (0: 기본, 1: 외부USB)
    "model_path": "model/defect_model.tflite",  # TFLite 모델 경로
    "model_h5_path": "model/defect_model.h5",   # H5 모델 (fallback)
    "class_indices_path": "model/class_indices.json",
    "defect_save_dir": "data/defects",          # 불량 이미지 저장 폴더
    "db_path": "data/defect_log.db",            # SQLite DB 경로
    "server_port": 5001,                         # 웹 서버 포트
    "img_size": (224, 224),                      # 모델 입력 크기
    "defect_threshold": 0.5,                     # 불량 판단 임계값 (이 값 이상이면 불량)
    "warn_defect_rate": 5.0,                     # 불량률 경고 임계값 (%)
    "inference_interval": 0.5,                   # 추론 간격 (초) - 라즈베리파이 부하 조절
    "recent_defect_show": 5,                     # 대시보드에 표시할 최근 불량 사진 수
    "dummy_mode": False,                         # True: 카메라 없이 테스트 모드
}

# ─────────────────────────────────────────────
# 전역 상태 (스레드 공유)
# ─────────────────────────────────────────────
state = {
    "running": False,
    "current_frame": None,          # 최신 카메라 프레임 (JPEG bytes)
    "last_result": "알 수 없음",    # 최신 판정 결과
    "last_confidence": 0.0,         # 최신 신뢰도
    "total_inspected": 0,           # 오늘 검사 수량
    "defect_count": 0,              # 오늘 불량 수량
    "defect_rate": 0.0,             # 불량률 (%)
    "recent_defects": [],           # 최근 불량 사진 경로 리스트
    "warning": False,               # 불량률 경고 여부
    "last_defect_time": None,       # 마지막 불량 감지 시각
    "model_loaded": False,          # 모델 로드 여부
    "dummy_mode": False,            # 더미 모드 여부
}
state_lock = threading.Lock()

# ─────────────────────────────────────────────
# SQLite DB 초기화
# ─────────────────────────────────────────────
def init_db():
    """불량 이력 DB 생성"""
    os.makedirs(os.path.dirname(CONFIG["db_path"]), exist_ok=True)
    conn = sqlite3.connect(CONFIG["db_path"])
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS defect_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,         -- ISO 8601 형식
            date        TEXT NOT NULL,         -- YYYY-MM-DD (오늘 집계용)
            result_type TEXT NOT NULL,         -- '불량' 또는 '정상'
            confidence  REAL NOT NULL,         -- 모델 신뢰도 (0.0~1.0)
            image_path  TEXT                   -- 저장된 이미지 경로 (불량만)
        )
    """)
    conn.commit()
    conn.close()
    print(f"[DB] 초기화 완료: {CONFIG['db_path']}")


def log_defect(result_type, confidence, image_path=None):
    """불량/정상 이력을 DB에 기록"""
    now = datetime.now()
    conn = sqlite3.connect(CONFIG["db_path"])
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO defect_log (timestamp, date, result_type, confidence, image_path) VALUES (?, ?, ?, ?, ?)",
        (now.isoformat(), now.strftime("%Y-%m-%d"), result_type, round(confidence, 4), image_path)
    )
    conn.commit()
    conn.close()


def get_today_stats():
    """오늘 날짜 기준 검사/불량 통계 조회"""
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(CONFIG["db_path"])
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM defect_log WHERE date = ?", (today,))
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM defect_log WHERE date = ? AND result_type = '불량'", (today,))
    defects = cur.fetchone()[0]
    cur.execute(
        "SELECT timestamp, confidence, image_path FROM defect_log WHERE date = ? AND result_type = '불량' ORDER BY id DESC LIMIT ?",
        (today, CONFIG["recent_defect_show"])
    )
    recent = cur.fetchall()
    conn.close()
    return total, defects, recent


# ─────────────────────────────────────────────
# AI 모델 로드
# ─────────────────────────────────────────────
def load_model():
    """TFLite 또는 Keras 모델 로드. 모델 없으면 더미 모드"""
    # 클래스 인덱스 로드 (불량=1 인지 확인)
    defect_index = 1  # 기본값: 불량이 인덱스 1
    if os.path.exists(CONFIG["class_indices_path"]):
        with open(CONFIG["class_indices_path"], "r", encoding="utf-8") as f:
            class_indices = json.load(f)
        # {"불량": 0, "정상": 1} 또는 {"정상": 0, "불량": 1} 형태
        defect_index = class_indices.get("불량", 1)
        print(f"[MODEL] 클래스 인덱스: {class_indices} → 불량 인덱스={defect_index}")

    # TFLite 모델 우선 시도
    if os.path.exists(CONFIG["model_path"]):
        try:
            import tflite_runtime.interpreter as tflite
            interpreter = tflite.Interpreter(model_path=CONFIG["model_path"])
            interpreter.allocate_tensors()
            input_details = interpreter.get_input_details()
            output_details = interpreter.get_output_details()
            print(f"[MODEL] TFLite 모델 로드 완료 (tflite_runtime): {CONFIG['model_path']}")

            def predict_tflite(img_array):
                interpreter.set_tensor(input_details[0]['index'], img_array)
                interpreter.invoke()
                output = interpreter.get_tensor(output_details[0]['index'])
                # sigmoid 출력 (이진 분류): 불량 확률
                confidence = float(output[0][0])
                if defect_index == 0:
                    confidence = 1.0 - confidence  # 불량이 인덱스 0이면 반전
                return confidence

            with state_lock:
                state["model_loaded"] = True
            return predict_tflite

        except ImportError:
            print("[MODEL] tflite_runtime 없음, tensorflow로 시도...")
            try:
                import tensorflow as tf
                interpreter = tf.lite.Interpreter(model_path=CONFIG["model_path"])
                interpreter.allocate_tensors()
                input_details = interpreter.get_input_details()
                output_details = interpreter.get_output_details()
                print(f"[MODEL] TFLite 모델 로드 완료 (tensorflow): {CONFIG['model_path']}")

                def predict_tflite_tf(img_array):
                    interpreter.set_tensor(input_details[0]['index'], img_array)
                    interpreter.invoke()
                    output = interpreter.get_tensor(output_details[0]['index'])
                    confidence = float(output[0][0])
                    if defect_index == 0:
                        confidence = 1.0 - confidence
                    return confidence

                with state_lock:
                    state["model_loaded"] = True
                return predict_tflite_tf

            except Exception as e:
                print(f"[MODEL] TFLite 로드 실패: {e}")

    # H5 모델 fallback
    if os.path.exists(CONFIG["model_h5_path"]):
        try:
            import tensorflow as tf
            model = tf.keras.models.load_model(CONFIG["model_h5_path"])
            print(f"[MODEL] Keras H5 모델 로드 완료: {CONFIG['model_h5_path']}")

            def predict_keras(img_array):
                output = model.predict(img_array, verbose=0)
                confidence = float(output[0][0])
                if defect_index == 0:
                    confidence = 1.0 - confidence
                return confidence

            with state_lock:
                state["model_loaded"] = True
            return predict_keras

        except Exception as e:
            print(f"[MODEL] Keras H5 모델 로드 실패: {e}")

    # 모델 파일 없음 → 더미 모드
    print("[WARN] 학습된 모델이 없습니다. 더미 모드로 실행합니다.")
    print("  → python3 train.py 를 먼저 실행하여 모델을 학습시키세요.")
    with state_lock:
        state["dummy_mode"] = True
        state["model_loaded"] = False

    def predict_dummy(img_array):
        """더미 예측: 랜덤 결과 (테스트용)"""
        import random
        return random.uniform(0.0, 1.0)  # 랜덤 신뢰도

    return predict_dummy


# ─────────────────────────────────────────────
# 이미지 전처리
# ─────────────────────────────────────────────
def preprocess_frame(frame):
    """카메라 프레임을 모델 입력 형식으로 변환"""
    img = cv2.resize(frame, CONFIG["img_size"])
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)  # BGR → RGB
    img = img.astype(np.float32) / 255.0        # 정규화 [0, 1]
    img = np.expand_dims(img, axis=0)            # (1, 224, 224, 3)
    return img


def create_dummy_frame():
    """카메라 없을 때 테스트용 더미 프레임 생성"""
    # 밝기가 시간에 따라 변하는 그라디언트 이미지
    h, w = 480, 640
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    t = time.time()
    color_val = int(abs(np.sin(t * 0.5)) * 200) + 30

    # 유부초밥 모양 시뮬레이션 (원형)
    cv2.rectangle(frame, (0, 0), (w, h), (20, 30, 40), -1)
    cv2.ellipse(frame, (w//2, h//2), (150, 80),
                int(t * 30) % 360, 0, 360,
                (color_val, color_val - 30, 50), -1)
    cv2.putText(frame, "DUMMY MODE - No Camera",
                (w//2 - 200, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (100, 200, 100), 2)
    cv2.putText(frame, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                (w//2 - 150, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 150, 150), 1)
    return frame


# ─────────────────────────────────────────────
# 카메라 캡처 + 추론 스레드
# ─────────────────────────────────────────────
def run_detection(predict_fn):
    """실시간 카메라 프레임 캡처 및 불량 추론"""
    os.makedirs(CONFIG["defect_save_dir"], exist_ok=True)

    # 카메라 열기
    cap = None
    use_dummy = CONFIG["dummy_mode"]

    if not use_dummy:
        cap = cv2.VideoCapture(CONFIG["camera_index"])
        if not cap.isOpened():
            print(f"[WARN] 카메라(index={CONFIG['camera_index']})를 열 수 없습니다. 더미 모드로 전환합니다.")
            use_dummy = True
            with state_lock:
                state["dummy_mode"] = True
        else:
            # 카메라 해상도 설정
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            print(f"[CAM] 카메라 열기 성공 (index={CONFIG['camera_index']})")

    with state_lock:
        state["running"] = True

    last_inference_time = 0

    print("[INFO] 불량 검출 시작...")

    while True:
        current_time = time.time()

        # 프레임 획득
        if use_dummy:
            frame = create_dummy_frame()
            time.sleep(0.033)  # ~30fps
        else:
            ret, frame = cap.read()
            if not ret:
                print("[WARN] 프레임 읽기 실패, 재시도...")
                time.sleep(0.1)
                continue

        # 추론 (inference_interval 간격으로)
        if current_time - last_inference_time >= CONFIG["inference_interval"]:
            last_inference_time = current_time

            # 모델 추론
            img_array = preprocess_frame(frame)
            try:
                defect_confidence = predict_fn(img_array)
            except Exception as e:
                print(f"[ERROR] 추론 실패: {e}")
                defect_confidence = 0.0

            is_defect = defect_confidence >= CONFIG["defect_threshold"]
            result_type = "불량" if is_defect else "정상"
            display_confidence = defect_confidence if is_defect else (1.0 - defect_confidence)

            # 불량 감지 시 이미지 저장 및 DB 기록
            saved_path = None
            if is_defect:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{ts}_{defect_confidence:.2f}.jpg"
                saved_path = os.path.join(CONFIG["defect_save_dir"], filename)
                cv2.imwrite(saved_path, frame)
                print(f"[DEFECT] 불량 감지! 신뢰도={defect_confidence:.1%} | 저장: {saved_path}")

            # DB 기록 (불량만 기록하여 DB 크기 절약, 통계는 별도 카운터 사용)
            log_defect(result_type, defect_confidence, saved_path)

            # 오늘 통계 조회
            total, defects, recent = get_today_stats()
            defect_rate = (defects / total * 100) if total > 0 else 0.0
            warning = defect_rate > CONFIG["warn_defect_rate"]

            # 최근 불량 사진 경로 (웹 접근용 상대 경로로 변환)
            recent_defect_paths = [
                {
                    "timestamp": r[0],
                    "confidence": r[1],
                    "path": f"/defect_image/{os.path.basename(r[2])}" if r[2] else None
                }
                for r in recent if r[2]
            ]

            # 상태 업데이트
            with state_lock:
                state["last_result"] = result_type
                state["last_confidence"] = display_confidence
                state["total_inspected"] = total
                state["defect_count"] = defects
                state["defect_rate"] = round(defect_rate, 2)
                state["recent_defects"] = recent_defect_paths
                state["warning"] = warning
                if is_defect:
                    state["last_defect_time"] = datetime.now().isoformat()

            # SocketIO로 실시간 상태 전송
            try:
                socketio.emit("update", {
                    "result": result_type,
                    "confidence": round(display_confidence * 100, 1),
                    "total": total,
                    "defects": defects,
                    "defect_rate": round(defect_rate, 2),
                    "warning": warning,
                    "recent_defects": recent_defect_paths,
                    "dummy_mode": state["dummy_mode"],
                    "model_loaded": state["model_loaded"],
                })
            except Exception:
                pass

        # 프레임에 판정 결과 오버레이
        frame_with_overlay = frame.copy()
        with state_lock:
            result = state["last_result"]
            conf = state["last_confidence"]
            warn = state["warning"]
            d_rate = state["defect_rate"]

        # 결과 텍스트 색상
        color = (0, 0, 255) if result == "불량" else (0, 255, 0)
        label = f"{result} ({conf:.1%})"
        cv2.rectangle(frame_with_overlay, (0, 0), (300, 70), (0, 0, 0), -1)
        cv2.putText(frame_with_overlay, label,
                    (10, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 2)

        # 불량률 표시
        rate_color = (0, 100, 255) if warn else (100, 200, 100)
        cv2.putText(frame_with_overlay, f"불량률: {d_rate:.1f}%",
                    (10, frame_with_overlay.shape[0] - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, rate_color, 2)

        # 경고 시 빨간 테두리
        if warn:
            h, w = frame_with_overlay.shape[:2]
            cv2.rectangle(frame_with_overlay, (0, 0), (w - 1, h - 1), (0, 0, 255), 8)

        # JPEG 인코딩 후 공유
        _, jpeg = cv2.imencode(".jpg", frame_with_overlay, [cv2.IMWRITE_JPEG_QUALITY, 75])
        with state_lock:
            state["current_frame"] = jpeg.tobytes()

    if cap:
        cap.release()


# ─────────────────────────────────────────────
# Flask 웹 서버 + SocketIO
# ─────────────────────────────────────────────
app = Flask(__name__)
app.config["SECRET_KEY"] = "heyfood-ai-secret"
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── 대시보드 HTML ──
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>헤이푸드 AI 불량 검출 대시보드</title>
<script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }

  header {
    background: linear-gradient(135deg, #1e293b, #0f172a);
    padding: 16px 24px;
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 2px solid #f59e0b;
  }
  header h1 { font-size: 1.3rem; color: #f59e0b; }
  .header-right { display: flex; align-items: center; gap: 16px; }
  .badge {
    padding: 4px 12px; border-radius: 20px; font-size: 0.8rem; font-weight: bold;
  }
  .badge.dummy { background: #92400e; color: #fde68a; }
  .badge.live   { background: #064e3b; color: #6ee7b7; }

  /* 경고 배너 */
  .alert-banner {
    display: none; padding: 14px; background: #7f1d1d;
    color: #fca5a5; text-align: center; font-size: 1.1rem; font-weight: bold;
    animation: blink 1s step-start infinite;
  }
  .alert-banner.show { display: block; }
  @keyframes blink { 50% { opacity: 0.4; } }

  /* 레이아웃 */
  .main { display: flex; gap: 16px; padding: 20px; flex-wrap: wrap; }
  .left  { flex: 1 1 400px; display: flex; flex-direction: column; gap: 16px; }
  .right { flex: 1 1 300px; display: flex; flex-direction: column; gap: 16px; }

  /* 카메라 피드 */
  .camera-box {
    background: #1e293b; border-radius: 12px; overflow: hidden;
    border: 2px solid #334155; position: relative;
  }
  .camera-box img { width: 100%; display: block; }
  .camera-label {
    position: absolute; top: 10px; right: 10px;
    padding: 4px 10px; border-radius: 6px; font-size: 0.85rem; font-weight: bold;
  }
  .camera-label.defect { background: rgba(220,38,38,0.85); color: white; }
  .camera-label.normal { background: rgba(5,150,105,0.85);  color: white; }

  /* 통계 카드 */
  .stats-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
  .card {
    background: #1e293b; border-radius: 12px; padding: 18px;
    text-align: center; border: 1px solid #334155;
  }
  .card .label { font-size: 0.75rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }
  .card .value { font-size: 2.2rem; font-weight: bold; }
  .card.ok   .value { color: #34d399; }
  .card.warn .value { color: #f87171; }
  .card.info .value { color: #f59e0b; }
  .card .unit { font-size: 0.8rem; color: #64748b; margin-top: 4px; }

  /* 현재 판정 */
  .result-card {
    background: #1e293b; border-radius: 12px; padding: 20px;
    border: 2px solid #334155; text-align: center; transition: border-color 0.3s;
  }
  .result-card.defect { border-color: #ef4444; background: #1c0a0a; }
  .result-card.normal { border-color: #10b981; background: #071c14; }
  .result-card .result-text { font-size: 2.5rem; font-weight: bold; margin: 8px 0; }
  .result-card.defect .result-text { color: #f87171; }
  .result-card.normal .result-text { color: #34d399; }
  .result-card .conf-text { font-size: 1.1rem; color: #94a3b8; }

  /* 최근 불량 이력 */
  .defect-history { background: #1e293b; border-radius: 12px; padding: 16px; border: 1px solid #334155; }
  .defect-history h3 { color: #f59e0b; margin-bottom: 12px; font-size: 0.95rem; }
  .defect-item {
    display: flex; align-items: center; gap: 10px;
    padding: 8px 0; border-bottom: 1px solid #334155;
  }
  .defect-item:last-child { border-bottom: none; }
  .defect-item img { width: 72px; height: 48px; object-fit: cover; border-radius: 6px; border: 1px solid #ef4444; }
  .defect-item .info { font-size: 0.8rem; color: #94a3b8; }
  .defect-item .info .time { color: #e2e8f0; font-weight: bold; }
  .no-defect { text-align: center; color: #475569; padding: 20px 0; font-size: 0.9rem; }

  footer { text-align: center; padding: 16px; color: #475569; font-size: 0.8rem; }
</style>
</head>
<body>

<header>
  <h1>🤖 헤이푸드 AI 불량 검출 대시보드</h1>
  <div class="header-right">
    <span id="modeBadge" class="badge live">● 라이브</span>
    <span id="clock" style="color:#94a3b8; font-size:1rem;"></span>
  </div>
</header>

<div class="alert-banner" id="alertBanner">
  ⚠️ 경고: 불량률이 {{ warn_threshold }}%를 초과했습니다! 즉시 확인하세요!
</div>

<div class="main">
  <!-- 왼쪽: 카메라 + 통계 -->
  <div class="left">
    <div class="camera-box">
      <img id="videoFeed" src="/video_feed" alt="카메라 피드" />
      <div class="camera-label normal" id="cameraLabel">● 정상</div>
    </div>

    <div class="stats-grid">
      <div class="card info">
        <div class="label">오늘 검사수량</div>
        <div class="value" id="totalCount">0</div>
        <div class="unit">개</div>
      </div>
      <div class="card warn">
        <div class="label">불량 수량</div>
        <div class="value" id="defectCount">0</div>
        <div class="unit">개</div>
      </div>
      <div class="card ok" id="rateCard">
        <div class="label">불량률</div>
        <div class="value" id="defectRate">0.0</div>
        <div class="unit">%</div>
      </div>
    </div>
  </div>

  <!-- 오른쪽: 판정 결과 + 불량 이력 -->
  <div class="right">
    <div class="result-card normal" id="resultCard">
      <div style="font-size:0.8rem; color:#94a3b8; text-transform:uppercase; letter-spacing:1px;">현재 판정</div>
      <div class="result-text" id="resultText">정상</div>
      <div class="conf-text">신뢰도: <span id="confText">0.0</span>%</div>
    </div>

    <div class="defect-history">
      <h3>📸 최근 불량 이력 (최근 {{ recent_count }}건)</h3>
      <div id="defectList">
        <div class="no-defect">아직 불량이 없습니다</div>
      </div>
    </div>
  </div>
</div>

<footer>헤이푸드서비스 AI 불량 검출 시스템 | 포트 5001 | 실시간 SocketIO 연결</footer>

<script>
const socket = io();

// 시계
function clock() {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('ko-KR');
}
setInterval(clock, 1000); clock();

// SocketIO 실시간 업데이트
socket.on('update', function(data) {
  // 모드 배지
  const badge = document.getElementById('modeBadge');
  if (data.dummy_mode) {
    badge.className = 'badge dummy';
    badge.textContent = '⚠ 더미모드';
  } else {
    badge.className = 'badge live';
    badge.textContent = '● 라이브';
  }

  // 판정 결과 카드
  const resultCard = document.getElementById('resultCard');
  const resultText = document.getElementById('resultText');
  const confText = document.getElementById('confText');
  const cameraLabel = document.getElementById('cameraLabel');

  resultText.textContent = data.result;
  confText.textContent = data.confidence.toFixed(1);

  if (data.result === '불량') {
    resultCard.className = 'result-card defect';
    cameraLabel.className = 'camera-label defect';
    cameraLabel.textContent = '● 불량';
  } else {
    resultCard.className = 'result-card normal';
    cameraLabel.className = 'camera-label normal';
    cameraLabel.textContent = '● 정상';
  }

  // 통계
  document.getElementById('totalCount').textContent = data.total.toLocaleString();
  document.getElementById('defectCount').textContent = data.defects.toLocaleString();
  document.getElementById('defectRate').textContent = data.defect_rate.toFixed(1);

  // 불량률 카드 색상
  const rateCard = document.getElementById('rateCard');
  rateCard.className = data.warning ? 'card warn' : 'card ok';

  // 경고 배너
  const alertBanner = document.getElementById('alertBanner');
  if (data.warning) {
    alertBanner.classList.add('show');
  } else {
    alertBanner.classList.remove('show');
  }

  // 최근 불량 이력
  const defectList = document.getElementById('defectList');
  if (data.recent_defects && data.recent_defects.length > 0) {
    defectList.innerHTML = data.recent_defects.map(d => {
      const ts = new Date(d.timestamp).toLocaleTimeString('ko-KR');
      const conf = (d.confidence * 100).toFixed(1);
      const imgSrc = d.path ? d.path : '';
      return `
        <div class="defect-item">
          ${imgSrc ? `<img src="${imgSrc}" alt="불량 이미지" onerror="this.style.display='none'">` : ''}
          <div class="info">
            <div class="time">${ts}</div>
            <div>신뢰도: ${conf}%</div>
          </div>
        </div>`;
    }).join('');
  } else {
    defectList.innerHTML = '<div class="no-defect">아직 불량이 없습니다</div>';
  }
});

socket.on('connect', () => console.log('SocketIO 연결됨'));
socket.on('disconnect', () => console.log('SocketIO 연결 끊김'));
</script>
</body>
</html>
"""


@app.route("/")
def dashboard():
    html = DASHBOARD_HTML.replace("{{ warn_threshold }}", str(CONFIG["warn_defect_rate"]))
    html = html.replace("{{ recent_count }}", str(CONFIG["recent_defect_show"]))
    return html


@app.route("/video_feed")
def video_feed():
    """MJPEG 스트리밍 엔드포인트"""
    def generate():
        while True:
            with state_lock:
                frame = state["current_frame"]
            if frame:
                yield (b"--frame\r\n"
                       b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
            time.sleep(0.05)  # ~20fps 스트리밍

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


@app.route("/defect_image/<filename>")
def serve_defect_image(filename):
    """불량 이미지 파일 서빙"""
    from flask import send_from_directory, abort
    # 경로 탐색 방지 (보안)
    safe_name = os.path.basename(filename)
    path = os.path.join(CONFIG["defect_save_dir"], safe_name)
    if os.path.exists(path):
        return send_from_directory(CONFIG["defect_save_dir"], safe_name)
    return abort(404)


@app.route("/api/stats")
def api_stats():
    """현재 통계 API"""
    with state_lock:
        return jsonify({
            "total_inspected": state["total_inspected"],
            "defect_count": state["defect_count"],
            "defect_rate": state["defect_rate"],
            "last_result": state["last_result"],
            "last_confidence": state["last_confidence"],
            "warning": state["warning"],
            "model_loaded": state["model_loaded"],
            "dummy_mode": state["dummy_mode"],
            "last_defect_time": state["last_defect_time"],
        })


@app.route("/api/history")
def api_history():
    """오늘 불량 이력 API"""
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(CONFIG["db_path"])
    cur = conn.cursor()
    cur.execute(
        "SELECT timestamp, result_type, confidence, image_path FROM defect_log WHERE date = ? ORDER BY id DESC LIMIT 100",
        (today,)
    )
    rows = cur.fetchall()
    conn.close()
    return jsonify([
        {"timestamp": r[0], "type": r[1], "confidence": r[2], "image_path": r[3]}
        for r in rows
    ])


@socketio.on("connect")
def on_connect():
    """클라이언트 연결 시 현재 상태 즉시 전송"""
    with state_lock:
        emit("update", {
            "result": state["last_result"],
            "confidence": round(state["last_confidence"] * 100, 1),
            "total": state["total_inspected"],
            "defects": state["defect_count"],
            "defect_rate": state["defect_rate"],
            "warning": state["warning"],
            "recent_defects": state["recent_defects"],
            "dummy_mode": state["dummy_mode"],
            "model_loaded": state["model_loaded"],
        })


# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  헤이푸드 AI 불량 검출 시스템 시작")
    print("=" * 55)

    # 1. DB 초기화
    init_db()

    # 2. 모델 로드
    predict_fn = load_model()

    # 3. 더미 모드 체크
    if CONFIG["dummy_mode"]:
        print("[INFO] 더미 모드: 카메라 없이 테스트 실행")
        CONFIG["dummy_mode"] = True

    # 4. 감지 스레드 시작
    detection_thread = threading.Thread(
        target=run_detection, args=(predict_fn,), daemon=True
    )
    detection_thread.start()

    print(f"[INFO] 대시보드: http://0.0.0.0:{CONFIG['server_port']}")
    print(f"[INFO] 불량률 경고 임계값: {CONFIG['warn_defect_rate']}%")
    print(f"[INFO] 이미지 저장 경로: {CONFIG['defect_save_dir']}/")
    print(f"[INFO] DB 경로: {CONFIG['db_path']}")
    print()

    # 5. Flask+SocketIO 서버 실행
    socketio.run(
        app,
        host="0.0.0.0",
        port=CONFIG["server_port"],
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )

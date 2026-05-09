#!/usr/bin/env python3
"""
헤이푸드 자동충진실링기 생산 카운팅 프로그램
라즈베리파이 + 카메라 기반 영상 분석으로 생산수량 자동 집계

실행: python3 counter2.py
"""

import cv2
import time
import json
import threading
from datetime import datetime
from flask import Flask, jsonify, render_template_string
from flask_cors import CORS

# ─────────────────────────────────────────────
# 설정값 (현장에서 조정)
# ─────────────────────────────────────────────
CONFIG = {
    "camera_index": 1,          # 카메라 번호 (0: 유부초밥 성형기, 1: 자동충진실링기)
    "line_position": 0.5,       # 기준선 위치 (화면 높이의 50% = 중앙)
    "min_area": 500,            # 감지할 최소 면적 (노이즈 제거)
    "stop_threshold_sec": 10,   # N초간 카운트 없으면 "정지중" 판단
    "server_port": 5001,        # 웹 서버 포트 (counter.py는 5000, 이건 5001)
    "machine_name": "자동충진실링기 1호기",
}

# ─────────────────────────────────────────────
# 전역 상태 (스레드 공유)
# ─────────────────────────────────────────────
state = {
    "count": 0,
    "status": "정지중",         # "가동중" / "정지중"
    "start_time": None,
    "last_count_time": None,
    "running_seconds": 0,
    "stop_seconds": 0,
    "speed_per_min": 0,
    "recent_counts": [],        # 최근 1분 카운트 기록
}
state_lock = threading.Lock()

# ─────────────────────────────────────────────
# 카운팅 로직
# ─────────────────────────────────────────────
def run_counter():
    cap = cv2.VideoCapture(CONFIG["camera_index"])
    if not cap.isOpened():
        print("[ERROR] 카메라를 열 수 없습니다. camera_index를 확인하세요.")
        return

    ret, prev_frame = cap.read()
    if not ret:
        print("[ERROR] 첫 프레임을 읽을 수 없습니다.")
        return

    h, w = prev_frame.shape[:2]
    line_y = int(h * CONFIG["line_position"])

    prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
    prev_gray = cv2.GaussianBlur(prev_gray, (21, 21), 0)

    # 기준선을 통과한 객체 추적용
    tracked_objects = {}
    next_id = 0

    # 상태 타이머
    last_status_update = time.time()

    print(f"[INFO] 카운팅 시작 | 기준선 Y={line_y}px | 포트={CONFIG['server_port']}")
    print(f"[INFO] 대시보드: http://localhost:{CONFIG['server_port']}")

    with state_lock:
        state["start_time"] = datetime.now().isoformat()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        # 프레임 차이로 움직임 감지
        diff = cv2.absdiff(prev_gray, gray)
        thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)

        contours, _ = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        current_time = time.time()

        for contour in contours:
            if cv2.contourArea(contour) < CONFIG["min_area"]:
                continue

            (x, y, cw, ch) = cv2.boundingRect(contour)
            center_y = y + ch // 2
            center_x = x + cw // 2

            # 기준선 통과 감지 (위→아래 또는 아래→위)
            matched = False
            for obj_id, obj in list(tracked_objects.items()):
                dist = abs(center_x - obj["x"])
                if dist < 80:  # 같은 객체로 판단하는 거리
                    prev_y = obj["y"]
                    # 기준선을 통과했는지 확인
                    if (prev_y < line_y <= center_y) or (prev_y > line_y >= center_y):
                        if not obj.get("counted"):
                            with state_lock:
                                state["count"] += 1
                                state["last_count_time"] = current_time
                                state["status"] = "가동중"
                                state["recent_counts"].append(current_time)
                                # 1분 이상 된 기록 제거
                                state["recent_counts"] = [t for t in state["recent_counts"] if current_time - t <= 60]
                                state["speed_per_min"] = len(state["recent_counts"])
                            obj["counted"] = True
                            print(f"[COUNT] {state['count']}개 | {datetime.now().strftime('%H:%M:%S')}")
                    obj["y"] = center_y
                    obj["x"] = center_x
                    obj["last_seen"] = current_time
                    matched = True
                    break

            if not matched:
                tracked_objects[next_id] = {
                    "x": center_x, "y": center_y,
                    "last_seen": current_time, "counted": False
                }
                next_id += 1

        # 오래된 추적 객체 제거
        tracked_objects = {
            k: v for k, v in tracked_objects.items()
            if current_time - v["last_seen"] < 2.0
        }

        # 가동/정지 상태 업데이트
        with state_lock:
            last_ct = state["last_count_time"]
            if last_ct and (current_time - last_ct) > CONFIG["stop_threshold_sec"]:
                state["status"] = "정지중"

            # 가동/정지 시간 누적
            elapsed = current_time - last_status_update
            if state["status"] == "가동중":
                state["running_seconds"] += elapsed
            else:
                state["stop_seconds"] += elapsed

        last_status_update = current_time
        prev_gray = gray

        # 디버그 화면 (라즈베리파이에 모니터 연결 시)
        try:
            cv2.line(frame, (0, line_y), (w, line_y), (0, 255, 0), 2)
            cv2.putText(frame, f"Count: {state['count']}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
            cv2.putText(frame, state["status"], (10, 70),
                        cv2.FONT_HERSHEY_SIMPLEX, 1,
                        (0, 255, 0) if state["status"] == "가동중" else (0, 0, 255), 2)
            cv2.imshow("Counter (q: 종료)", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
        except Exception:
            pass  # 헤드리스 환경에서는 무시

    cap.release()
    cv2.destroyAllWindows()


# ─────────────────────────────────────────────
# Flask 웹 서버 (API + 대시보드)
# ─────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>헤이푸드 자동충진실링기 대시보드</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'Segoe UI', sans-serif; background: #0f172a; color: #e2e8f0; min-height: 100vh; }
  header {
    background: linear-gradient(135deg, #1e293b, #0f172a);
    padding: 20px 30px;
    display: flex; align-items: center; justify-content: space-between;
    border-bottom: 2px solid #f59e0b;
  }
  header h1 { font-size: 1.4rem; color: #f59e0b; }
  header .time { font-size: 1.1rem; color: #94a3b8; }
  .status-bar {
    padding: 12px 30px;
    font-size: 1.1rem; font-weight: bold; text-align: center;
    transition: background 0.5s;
  }
  .status-bar.running { background: #065f46; color: #6ee7b7; }
  .status-bar.stopped { background: #7f1d1d; color: #fca5a5; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; padding: 30px; }
  .card {
    background: #1e293b; border-radius: 16px; padding: 24px;
    text-align: center; border: 1px solid #334155;
    transition: transform 0.2s;
  }
  .card:hover { transform: translateY(-4px); }
  .card .label { font-size: 0.85rem; color: #94a3b8; margin-bottom: 10px; text-transform: uppercase; letter-spacing: 1px; }
  .card .value { font-size: 2.8rem; font-weight: bold; color: #f59e0b; }
  .card .unit { font-size: 0.9rem; color: #64748b; margin-top: 6px; }
  .card.highlight .value { color: #34d399; }
  .card.warn .value { color: #f87171; }
  footer { text-align: center; padding: 20px; color: #475569; font-size: 0.85rem; }
</style>
</head>
<body>
<header>
  <h1>📦 헤이푸드 자동충진실링기 대시보드</h1>
  <span class="time" id="clock"></span>
</header>
<div class="status-bar stopped" id="statusBar">⏸ 정지중</div>
<div class="grid">
  <div class="card highlight">
    <div class="label">오늘 생산수량</div>
    <div class="value" id="count">0</div>
    <div class="unit">개</div>
  </div>
  <div class="card">
    <div class="label">분당 생산속도</div>
    <div class="value" id="speed">0</div>
    <div class="unit">개/분</div>
  </div>
  <div class="card highlight">
    <div class="label">누적 가동시간</div>
    <div class="value" id="running">00:00:00</div>
    <div class="unit">시:분:초</div>
  </div>
  <div class="card warn">
    <div class="label">누적 정지시간</div>
    <div class="value" id="stopped">00:00:00</div>
    <div class="unit">시:분:초</div>
  </div>
  <div class="card">
    <div class="label">작업 시작</div>
    <div class="value" id="startTime" style="font-size:1.4rem">--:--</div>
    <div class="unit">시각</div>
  </div>
  <div class="card">
    <div class="label">장비명</div>
    <div class="value" style="font-size:1rem; padding-top:10px" id="machineName">-</div>
    <div class="unit">&nbsp;</div>
  </div>
</div>
<footer>헤이푸드서비스 | 자동 갱신 2초마다</footer>
<script>
function fmt(sec) {
  const h = String(Math.floor(sec/3600)).padStart(2,'0');
  const m = String(Math.floor((sec%3600)/60)).padStart(2,'0');
  const s = String(Math.floor(sec%60)).padStart(2,'0');
  return h+':'+m+':'+s;
}
function clock() {
  const now = new Date();
  document.getElementById('clock').textContent = now.toLocaleTimeString('ko-KR');
}
setInterval(clock, 1000); clock();

async function update() {
  try {
    const res = await fetch('/api/state');
    const d = await res.json();
    document.getElementById('count').textContent = d.count.toLocaleString();
    document.getElementById('speed').textContent = d.speed_per_min;
    document.getElementById('running').textContent = fmt(d.running_seconds);
    document.getElementById('stopped').textContent = fmt(d.stop_seconds);
    document.getElementById('machineName').textContent = d.machine_name;
    if (d.start_time) {
      const t = new Date(d.start_time);
      document.getElementById('startTime').textContent =
        t.getHours().toString().padStart(2,'0')+':'+t.getMinutes().toString().padStart(2,'0');
    }
    const bar = document.getElementById('statusBar');
    if (d.status === '가동중') {
      bar.className = 'status-bar running';
      bar.textContent = '▶ 가동중';
    } else {
      bar.className = 'status-bar stopped';
      bar.textContent = '⏸ 정지중';
    }
  } catch(e) {}
}
setInterval(update, 2000); update();
</script>
</body>
</html>
"""

@app.route("/")
def dashboard():
    return DASHBOARD_HTML

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify({
            "count": state["count"],
            "status": state["status"],
            "start_time": state["start_time"],
            "running_seconds": round(state["running_seconds"]),
            "stop_seconds": round(state["stop_seconds"]),
            "speed_per_min": state["speed_per_min"],
            "machine_name": CONFIG["machine_name"],
        })

@app.route("/api/reset", methods=["POST"])
def api_reset():
    """하루 시작 시 카운트 초기화"""
    with state_lock:
        state["count"] = 0
        state["running_seconds"] = 0
        state["stop_seconds"] = 0
        state["start_time"] = datetime.now().isoformat()
        state["recent_counts"] = []
        state["speed_per_min"] = 0
    return jsonify({"ok": True})


# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # 카운팅 스레드 백그라운드 실행
    counter_thread = threading.Thread(target=run_counter, daemon=True)
    counter_thread.start()

    # Flask 서버 실행 (모든 인터페이스에서 접근 가능)
    print(f"[INFO] 서버 시작 | http://0.0.0.0:{CONFIG['server_port']}")
    app.run(host="0.0.0.0", port=CONFIG["server_port"], debug=False, use_reloader=False)

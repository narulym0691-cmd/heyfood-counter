#!/usr/bin/env python3
"""
헤이푸드 금속검출기 NMD500 통신 프로그램
RS-232C → USB 어댑터 → 라즈베리파이 연결
실행: python3 metal_detector.py

NMD500 패킷 형식:
 D  26-05-12  09:24:30  01    200\r\n
 ↑  ↑날짜     ↑시간    ↑품번 ↑검출수량
 검출코드(D=검출, P=전원ON, A=에이징)
"""

import serial
import threading
import time
import re
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
CONFIG = {
    "port": "/dev/ttyUSB0",     # USB 시리얼 포트 (라즈베리파이 기본값)
    "baudrate": 9600,           # NMD500 통신 속도
    "bytesize": 8,
    "parity": "N",              # None
    "stopbits": 1,
    "timeout": 1,
    "server_port": 5002,        # Flask API 포트
    # ─────────────────────────────────────────────
    # 클라우드 PUSH 설정 (2026-05-14 추가, 현재 준비 중)
    # ─────────────────────────────────────────────
    "machine_id": "metal",
    "machine_name": "금속검출기",
    "location": "-",
    "device_type": "metal",
    "enable_cloud_push": False,  # 운영 시작하면 True로 변경
}

# 클라우드 PUSH 모듈 import
try:
    from cloud_push import CloudPusher
    _CLOUD_PUSH_AVAILABLE = True
except Exception as _e:
    _CLOUD_PUSH_AVAILABLE = False

# ─────────────────────────────────────────────
# 전역 상태
# ─────────────────────────────────────────────
state = {
    "status": "정상",           # "정상" / "검출!" / "오프라인"
    "today_count": 0,           # 오늘 총 검출 수량
    "total_pass": 0,            # 오늘 총 통과 수량
    "last_detect_time": None,   # 마지막 검출 시각
    "last_detect_qty": 0,       # 마지막 검출 수량
    "product_no": "--",         # 현재 품번
    "alert": False,             # 경보 상태
    "connected": False,         # 시리얼 연결 상태
    "last_packet_time": None,   # 마지막 패킷 수신 시각
    "recent_detects": [],       # 최근 검출 이력 (최대 10건)
    "start_time": datetime.now().isoformat(),
}
state_lock = threading.Lock()

# ─────────────────────────────────────────────
# NMD500 패킷 파싱
# ─────────────────────────────────────────────
def parse_packet(line: str):
    """
    패킷 예시: " D  26-05-12  09:24:30  01    200"
    반환: {"code": "D", "date": "26-05-12", "time": "09:24:30", "product": "01", "qty": 200}
    """
    line = line.strip()
    # 정규식으로 파싱
    pattern = r'([DPA])\s+(\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+(\d{1,2})\s+(\d+)'
    match = re.search(pattern, line)
    if match:
        return {
            "code": match.group(1),
            "date": match.group(2),
            "time": match.group(3),
            "product": match.group(4).zfill(2),
            "qty": int(match.group(5)),
        }
    return None

# ─────────────────────────────────────────────
# 시리얼 수신 스레드
# ─────────────────────────────────────────────
def run_serial():
    while True:
        try:
            print(f"[INFO] 시리얼 연결 시도: {CONFIG['port']} ({CONFIG['baudrate']}bps)")
            ser = serial.Serial(
                port=CONFIG["port"],
                baudrate=CONFIG["baudrate"],
                bytesize=CONFIG["bytesize"],
                parity=CONFIG["parity"],
                stopbits=CONFIG["stopbits"],
                timeout=CONFIG["timeout"],
            )
            with state_lock:
                state["connected"] = True
                state["status"] = "정상"
            print(f"[INFO] 연결 성공! 데이터 수신 대기 중...")

            while True:
                try:
                    line = ser.readline().decode("ascii", errors="ignore")
                    if not line.strip():
                        continue

                    print(f"[RAW] {repr(line)}")
                    packet = parse_packet(line)
                    if not packet:
                        continue

                    now_str = datetime.now().strftime("%H:%M:%S")

                    with state_lock:
                        state["last_packet_time"] = now_str
                        state["product_no"] = packet["product"]

                        if packet["code"] == "D":
                            # 금속 검출!
                            state["status"] = "검출!"
                            state["alert"] = True
                            state["today_count"] += 1
                            state["last_detect_time"] = now_str
                            state["last_detect_qty"] = packet["qty"]
                            state["total_pass"] += packet["qty"]

                            # 최근 이력 추가 (최대 10건)
                            state["recent_detects"].insert(0, {
                                "time": now_str,
                                "qty": packet["qty"],
                                "product": packet["product"],
                            })
                            state["recent_detects"] = state["recent_detects"][:10]

                            print(f"[🚨 검출!] 품번:{packet['product']} 수량:{packet['qty']} 시각:{now_str}")

                        elif packet["code"] == "P":
                            # 전원 ON (정상 통과)
                            state["status"] = "정상"
                            state["alert"] = False
                            state["total_pass"] += packet["qty"]
                            print(f"[✅ 정상] 품번:{packet['product']} 수량:{packet['qty']}")

                        elif packet["code"] == "A":
                            # 에이징 모드
                            print(f"[⚙️ 에이징] 품번:{packet['product']}")

                except Exception as e:
                    print(f"[ERROR] 수신 오류: {e}")
                    break

            ser.close()

        except serial.SerialException as e:
            print(f"[ERROR] 포트 연결 실패: {e}")
            with state_lock:
                state["connected"] = False
                state["status"] = "오프라인"

        print("[INFO] 5초 후 재연결 시도...")
        time.sleep(5)

# ─────────────────────────────────────────────
# 경보 자동 해제 (30초 후)
# ─────────────────────────────────────────────
def alert_reset_watcher():
    while True:
        time.sleep(5)
        with state_lock:
            if state["alert"] and state["last_detect_time"]:
                try:
                    last = datetime.strptime(state["last_detect_time"], "%H:%M:%S")
                    now = datetime.now()
                    last_dt = now.replace(hour=last.hour, minute=last.minute, second=last.second)
                    if (now - last_dt).total_seconds() > 30:
                        state["alert"] = False
                        state["status"] = "정상"
                except Exception:
                    pass

# ─────────────────────────────────────────────
# Flask API
# ─────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify({
            "status": state["status"],
            "alert": state["alert"],
            "connected": state["connected"],
            "today_count": state["today_count"],
            "total_pass": state["total_pass"],
            "last_detect_time": state["last_detect_time"],
            "last_detect_qty": state["last_detect_qty"],
            "product_no": state["product_no"],
            "last_packet_time": state["last_packet_time"],
            "recent_detects": state["recent_detects"],
            "start_time": state["start_time"],
        })

@app.route("/api/reset", methods=["POST"])
def api_reset():
    """하루 시작 시 카운트 초기화"""
    with state_lock:
        state["today_count"] = 0
        state["total_pass"] = 0
        state["last_detect_time"] = None
        state["last_detect_qty"] = 0
        state["recent_detects"] = []
        state["alert"] = False
        state["status"] = "정상"
        state["start_time"] = datetime.now().isoformat()
    return jsonify({"ok": True})

@app.route("/api/test_detect", methods=["POST"])
def api_test():
    """테스트용 금속 검출 시뮬레이션 (실제 장비 없을 때)"""
    now_str = datetime.now().strftime("%H:%M:%S")
    with state_lock:
        state["status"] = "검출!"
        state["alert"] = True
        state["today_count"] += 1
        state["last_detect_time"] = now_str
        state["last_detect_qty"] = 1
        state["recent_detects"].insert(0, {
            "time": now_str,
            "qty": 1,
            "product": "01",
        })
        state["recent_detects"] = state["recent_detects"][:10]
    return jsonify({"ok": True, "message": "테스트 검출 발생!"})

# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # 시리얼 수신 스레드
    serial_thread = threading.Thread(target=run_serial, daemon=True)
    serial_thread.start()

    # 경보 자동 해제 스레드
    alert_thread = threading.Thread(target=alert_reset_watcher, daemon=True)
    alert_thread.start()

    print(f"[INFO] 금속검출기 서버 시작 | 포트: {CONFIG['server_port']}")
    print(f"[INFO] API: http://0.0.0.0:{CONFIG['server_port']}/api/state")
    print(f"[INFO] 테스트: POST http://0.0.0.0:{CONFIG['server_port']}/api/test_detect")
    app.run(host="0.0.0.0", port=CONFIG["server_port"], debug=False, use_reloader=False)

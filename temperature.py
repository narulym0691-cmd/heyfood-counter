#!/usr/bin/env python3
"""
헤이푸드 냉장/냉동고 온도 모니터링 프로그램
라즈베리파이 + DHT22 센서 2개 (GPIO 직접 연결)

센서 배치:
  - GPIO 4번  → DHT22 센서 1번 (냉장고)
  - GPIO 17번 → DHT22 센서 2번 (냉동고)

실행: python3 temperature.py
설치: pip3 install adafruit-circuitpython-dht flask flask-cors
      sudo apt-get install libgpiod2
"""

import threading
import time
from datetime import datetime
from flask import Flask, jsonify
from flask_cors import CORS

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
CONFIG = {
    "sensor1_pin": 4,           # GPIO 4번 → 냉장고
    "sensor2_pin": 17,          # GPIO 17번 → 냉동고
    "read_interval_sec": 10,    # 10초마다 온도 읽기
    "server_port": 5003,        # Flask API 포트
    # 온도 경보 기준
    "fridge_temp_max": 10.0,    # 냉장고 최대 허용 온도 (°C)
    "fridge_temp_min": 0.0,     # 냉장고 최소 허용 온도 (°C)
    "freezer_temp_max": -10.0,  # 냉동고 최대 허용 온도 (°C)
    "freezer_temp_min": -25.0,  # 냉동고 최소 허용 온도 (°C)
}

# ─────────────────────────────────────────────
# 전역 상태
# ─────────────────────────────────────────────
state = {
    "fridge": {
        "name": "냉장고",
        "temp": None,
        "humidity": None,
        "status": "정상",       # "정상" / "⚠ 온도 초과!" / "오프라인"
        "alert": False,
        "last_read": None,
        "temp_max": None,
        "temp_min": None,
        "connected": False,
    },
    "freezer": {
        "name": "냉동고",
        "temp": None,
        "humidity": None,
        "status": "정상",
        "alert": False,
        "last_read": None,
        "temp_max": None,
        "temp_min": None,
        "connected": False,
    },
    "start_time": datetime.now().isoformat(),
}
state_lock = threading.Lock()

# ─────────────────────────────────────────────
# DHT22 읽기
# ─────────────────────────────────────────────
def read_dht22(pin):
    """DHT22 센서에서 온도/습도 읽기. 실패 시 None 반환"""
    try:
        import board
        import adafruit_dht
        pin_obj = getattr(board, f"D{pin}")
        sensor = adafruit_dht.DHT22(pin_obj, use_pulseio=False)
        temp = sensor.temperature
        humidity = sensor.humidity
        sensor.exit()
        return temp, humidity
    except Exception as e:
        print(f"[ERROR] GPIO {pin} 읽기 실패: {e}")
        return None, None

def check_alert(temp, sensor_type):
    """온도 경보 판단"""
    if temp is None:
        return False, "오프라인"
    if sensor_type == "fridge":
        if temp > CONFIG["fridge_temp_max"]:
            return True, f"⚠ 온도 초과! ({temp:.1f}°C)"
        if temp < CONFIG["fridge_temp_min"]:
            return True, f"⚠ 온도 이상! ({temp:.1f}°C)"
    elif sensor_type == "freezer":
        if temp > CONFIG["freezer_temp_max"]:
            return True, f"⚠ 온도 초과! ({temp:.1f}°C)"
        if temp < CONFIG["freezer_temp_min"]:
            return True, f"⚠ 온도 이상! ({temp:.1f}°C)"
    return False, "정상"

# ─────────────────────────────────────────────
# 온도 읽기 스레드
# ─────────────────────────────────────────────
def run_temperature():
    while True:
        now_str = datetime.now().strftime("%H:%M:%S")

        # 냉장고 읽기
        temp1, hum1 = read_dht22(CONFIG["sensor1_pin"])
        alert1, status1 = check_alert(temp1, "fridge")
        with state_lock:
            s = state["fridge"]
            s["temp"] = round(temp1, 1) if temp1 is not None else None
            s["humidity"] = round(hum1, 1) if hum1 is not None else None
            s["alert"] = alert1
            s["status"] = status1
            s["last_read"] = now_str
            s["connected"] = temp1 is not None
            if temp1 is not None:
                s["temp_max"] = max(s["temp_max"], temp1) if s["temp_max"] else temp1
                s["temp_min"] = min(s["temp_min"], temp1) if s["temp_min"] else temp1
        print(f"[냉장고] {temp1}°C / {hum1}% → {status1}")

        time.sleep(1)  # 센서 안정화

        # 냉동고 읽기
        temp2, hum2 = read_dht22(CONFIG["sensor2_pin"])
        alert2, status2 = check_alert(temp2, "freezer")
        with state_lock:
            s = state["freezer"]
            s["temp"] = round(temp2, 1) if temp2 is not None else None
            s["humidity"] = round(hum2, 1) if hum2 is not None else None
            s["alert"] = alert2
            s["status"] = status2
            s["last_read"] = now_str
            s["connected"] = temp2 is not None
            if temp2 is not None:
                s["temp_max"] = max(s["temp_max"], temp2) if s["temp_max"] else temp2
                s["temp_min"] = min(s["temp_min"], temp2) if s["temp_min"] else temp2
        print(f"[냉동고] {temp2}°C / {hum2}% → {status2}")

        time.sleep(CONFIG["read_interval_sec"])

# ─────────────────────────────────────────────
# Flask API
# ─────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

@app.route("/api/state")
def api_state():
    with state_lock:
        return jsonify({
            "fridge": {
                "name": state["fridge"]["name"],
                "temp": state["fridge"]["temp"],
                "humidity": state["fridge"]["humidity"],
                "status": state["fridge"]["status"],
                "alert": state["fridge"]["alert"],
                "last_read": state["fridge"]["last_read"],
                "temp_max": state["fridge"]["temp_max"],
                "temp_min": state["fridge"]["temp_min"],
                "connected": state["fridge"]["connected"],
            },
            "freezer": {
                "name": state["freezer"]["name"],
                "temp": state["freezer"]["temp"],
                "humidity": state["freezer"]["humidity"],
                "status": state["freezer"]["status"],
                "alert": state["freezer"]["alert"],
                "last_read": state["freezer"]["last_read"],
                "temp_max": state["freezer"]["temp_max"],
                "temp_min": state["freezer"]["temp_min"],
                "connected": state["freezer"]["connected"],
            },
            "start_time": state["start_time"],
        })

@app.route("/api/reset", methods=["POST"])
def api_reset():
    """하루 시작 시 최고/최저 온도 초기화"""
    with state_lock:
        state["fridge"]["temp_max"] = None
        state["fridge"]["temp_min"] = None
        state["freezer"]["temp_max"] = None
        state["freezer"]["temp_min"] = None
        state["start_time"] = datetime.now().isoformat()
    return jsonify({"ok": True})

@app.route("/api/test", methods=["POST"])
def api_test():
    """테스트용 온도 데이터 주입 (센서 없을 때)"""
    now_str = datetime.now().strftime("%H:%M:%S")
    with state_lock:
        state["fridge"]["temp"] = 4.5
        state["fridge"]["humidity"] = 72.0
        state["fridge"]["status"] = "정상"
        state["fridge"]["alert"] = False
        state["fridge"]["last_read"] = now_str
        state["fridge"]["connected"] = True
        state["fridge"]["temp_max"] = 5.2
        state["fridge"]["temp_min"] = 3.8

        state["freezer"]["temp"] = -18.5
        state["freezer"]["humidity"] = 55.0
        state["freezer"]["status"] = "정상"
        state["freezer"]["alert"] = False
        state["freezer"]["last_read"] = now_str
        state["freezer"]["connected"] = True
        state["freezer"]["temp_max"] = -17.2
        state["freezer"]["temp_min"] = -19.8
    return jsonify({"ok": True, "message": "테스트 데이터 주입 완료!"})

# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    # 온도 읽기 스레드
    temp_thread = threading.Thread(target=run_temperature, daemon=True)
    temp_thread.start()

    print(f"[INFO] 온도 모니터링 서버 시작 | 포트: {CONFIG['server_port']}")
    print(f"[INFO] 냉장고 → GPIO {CONFIG['sensor1_pin']}번")
    print(f"[INFO] 냉동고 → GPIO {CONFIG['sensor2_pin']}번")
    print(f"[INFO] API: http://0.0.0.0:{CONFIG['server_port']}/api/state")
    print(f"[INFO] 테스트: POST http://0.0.0.0:{CONFIG['server_port']}/api/test")
    app.run(host="0.0.0.0", port=CONFIG["server_port"], debug=False, use_reloader=False)

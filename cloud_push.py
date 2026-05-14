#!/usr/bin/env python3
"""
헤이푸드 통합 관제 시스템 - 클라우드 PUSH 모듈

각 라즈베리에서 측정한 상태값을 Firebase Realtime DB에 5초마다 자동 전송합니다.
- 인터넷 끊겨도 카운팅은 계속 (로컬 큐에 버퍼링)
- 인터넷 복귀 시 자동 재시도
- 각 장비는 자기 machine_id 노드만 PUT

사용법:
    from cloud_push import CloudPusher
    pusher = CloudPusher(machine_id='yooboo')
    pusher.start()
    
    # state가 바뀔 때마다 호출 (5초마다 자동 전송)
    pusher.update(state_dict)
"""

import json
import time
import threading
import urllib.request
import urllib.error
import os
from datetime import datetime

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
DEFAULT_FIREBASE_URL = "https://heyfood-recipe-system-default-rtdb.asia-southeast1.firebasedatabase.app"
DEFAULT_PUSH_INTERVAL_SEC = 5  # 5초마다 PUSH
DEFAULT_TIMEOUT_SEC = 3        # HTTP timeout
DEFAULT_MAX_RETRY = 3          # 실패 시 재시도 횟수

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config():
    """config.json에서 설정 로드"""
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"[CloudPusher] config.json 로드 실패: {e}")
        return {}


class CloudPusher:
    """
    Firebase Realtime DB로 상태를 주기적으로 PUSH하는 모듈
    
    각 장비 노드: /factory/{machine_id}
    
    인증 방식:
        - 현재: 보안 규칙에서 ".write": true 로 모두 허용 (개발 단계)
        - 향후: Firebase Auth Custom Token으로 machine_id 기반 인증 강화
    """
    
    def __init__(self, machine_id, machine_name=None, location=None, device_type='counter',
                 firebase_url=None, push_interval=None):
        cfg = load_config()
        
        self.machine_id = machine_id or cfg.get('machine_id', 'unknown')
        self.machine_name = machine_name or cfg.get('machine_name', self.machine_id)
        self.location = location or cfg.get('location', '-')
        self.device_type = device_type or cfg.get('device_type', 'counter')
        
        self.firebase_url = (firebase_url or 
                            cfg.get('firebase_url') or 
                            DEFAULT_FIREBASE_URL).rstrip('/')
        self.push_interval = push_interval or cfg.get('push_interval_sec', DEFAULT_PUSH_INTERVAL_SEC)
        
        # 노드 URL
        self.endpoint = f"{self.firebase_url}/factory/{self.machine_id}.json"
        
        # 상태
        self.last_state = {}
        self.state_lock = threading.Lock()
        self.running = False
        self.thread = None
        
        # 통계
        self.push_success_count = 0
        self.push_fail_count = 0
        self.last_push_time = None
        self.last_error = None
        
        print(f"[CloudPusher] 초기화 완료")
        print(f"  - machine_id: {self.machine_id}")
        print(f"  - machine_name: {self.machine_name}")
        print(f"  - endpoint: {self.endpoint}")
        print(f"  - push_interval: {self.push_interval}초")
    
    def update(self, state_dict):
        """
        외부에서 호출 - 현재 상태값을 업데이트 (다음 PUSH 사이클에 전송됨)
        
        Args:
            state_dict: 전송할 상태 dict (count, status, running_seconds 등)
        """
        with self.state_lock:
            # 메타 정보 추가
            self.last_state = {
                **state_dict,
                'machine_id': self.machine_id,
                'machine_name': self.machine_name,
                'location': self.location,
                'device_type': self.device_type,
                'last_updated': int(time.time() * 1000),  # ms
                'last_updated_iso': datetime.now().isoformat(),
            }
    
    def _push_once(self):
        """단일 PUSH 시도 (PUT)"""
        with self.state_lock:
            if not self.last_state:
                return False
            data = dict(self.last_state)
        
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        
        for attempt in range(DEFAULT_MAX_RETRY):
            try:
                req = urllib.request.Request(
                    self.endpoint,
                    data=body,
                    method='PUT',
                    headers={'Content-Type': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SEC) as resp:
                    if resp.status == 200:
                        self.push_success_count += 1
                        self.last_push_time = time.time()
                        self.last_error = None
                        return True
                    else:
                        self.last_error = f"HTTP {resp.status}"
            except urllib.error.HTTPError as e:
                self.last_error = f"HTTPError {e.code}: {e.reason}"
                if e.code in (401, 403):
                    # 인증 오류는 재시도해도 안 됨
                    break
            except urllib.error.URLError as e:
                self.last_error = f"URLError: {e.reason}"
            except Exception as e:
                self.last_error = f"Exception: {e}"
            
            # 재시도 전 잠시 대기
            if attempt < DEFAULT_MAX_RETRY - 1:
                time.sleep(1)
        
        self.push_fail_count += 1
        return False
    
    def _run_loop(self):
        """백그라운드 루프 - push_interval 마다 PUSH"""
        print(f"[CloudPusher] 푸시 루프 시작 ({self.push_interval}초 간격)")
        
        while self.running:
            success = self._push_once()
            
            if not success and self.last_error:
                print(f"[CloudPusher] ❌ PUSH 실패: {self.last_error}")
            
            # 1시간마다 통계 출력
            if self.push_success_count > 0 and self.push_success_count % 720 == 0:
                print(f"[CloudPusher] 📊 통계: 성공 {self.push_success_count}, "
                      f"실패 {self.push_fail_count}")
            
            time.sleep(self.push_interval)
    
    def start(self):
        """백그라운드 PUSH 시작"""
        if self.running:
            print("[CloudPusher] 이미 실행 중")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        print(f"[CloudPusher] ✅ 시작됨 (machine_id={self.machine_id})")
    
    def stop(self):
        """PUSH 중단"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=10)
        print("[CloudPusher] 중단됨")
    
    def get_stats(self):
        """현재 PUSH 통계 반환 (Flask /api/state에서 노출용)"""
        return {
            'cloud_machine_id': self.machine_id,
            'cloud_push_success': self.push_success_count,
            'cloud_push_fail': self.push_fail_count,
            'cloud_last_push': self.last_push_time,
            'cloud_last_error': self.last_error,
        }


# ─────────────────────────────────────────────
# 테스트용 (직접 실행 시)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("[CloudPusher] 단독 실행 모드 (테스트)")
    
    pusher = CloudPusher(
        machine_id='test',
        machine_name='테스트 장비',
        location='테스트',
    )
    
    pusher.update({
        'count': 100,
        'status': '가동중',
        'speed_per_min': 30,
    })
    
    pusher.start()
    
    try:
        time.sleep(15)
        print("\n=== 통계 ===")
        print(json.dumps(pusher.get_stats(), indent=2, ensure_ascii=False))
    except KeyboardInterrupt:
        pass
    finally:
        pusher.stop()

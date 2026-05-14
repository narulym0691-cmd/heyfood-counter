# 헤이푸드 통합 관제 시스템 — 라즈베리파이 + 클라우드

라즈베리파이의 카메라 / 센서로 공장 데이터를 수집하고, **Firebase Realtime DB로 실시간 전송**하여 어디서든 모니터링 가능한 시스템입니다.

## 🏭 운영 구조 (2026-05-14 클라우드 전환)

```
[1층] 라즈베리 #1
 └─ DHT22 센서 (냉장고/냉동고)
    └─ temperature.py → Firebase /factory/temp

[2층] 라즈베리 #2
 ├─ 카메라 0 → counter.py  → Firebase /factory/yooboo  (유부초밥 성형기)
 └─ 카메라 1 → counter2.py → Firebase /factory/filling (자동충진 실링기)

[3층 모니터 / 어디든] 
 └─ 통합 관제 대시보드 (HTML)
    └─ Firebase Realtime DB 실시간 구독 → 자동 갱신
```

## ✨ 측정 항목

| 장비 | 파일 | 포트 | machine_id | 측정 |
|------|------|------|-----------|------|
| 유부초밥 성형기 | `counter.py` | 5000 | `yooboo` | 생산수량 / 분당속도 / 가동시간 |
| 자동충진 실링기 | `counter2.py` | 5001 | `filling` | 생산수량 / 분당속도 / 가동시간 |
| 금속검출기 | `metal_detector.py` | 5002 | `metal` | 검출 알람 (준비 중) |
| 1층 온습도 | `temperature.py` | 5003 | `temp` | 냉장고/냉동고 온도/습도 |

## 🚀 라즈베리파이 설치 (한 번만)

```bash
git clone https://github.com/narulym0691-cmd/heyfood-counter
cd heyfood-counter

bash install.sh    # OpenCV + Flask 설치
```

## 🔧 라즈베리파이별 자동 실행 등록

### 1층 라즈베리 (온습도)
```bash
cd heyfood-counter
bash autostart.sh temp
```

### 2층 라즈베리 (유부초밥 + 실링 동시)
```bash
cd heyfood-counter
bash autostart.sh all-2f
```

## 🔄 업데이트 (코드 수정 시)

각 라즈베리에서:
```bash
cd heyfood-counter
git pull

# 서비스 재시작
sudo systemctl restart heyfood-yooboo    # 2층, 유부초밥
sudo systemctl restart heyfood-filling   # 2층, 실링
sudo systemctl restart heyfood-temp      # 1층, 온도
```

## 📡 클라우드 연동 (자동)

각 .py 파일은 **자동으로 Firebase Realtime DB에 5초마다 PUSH** 합니다.

- 인터넷 끊겨도 카운팅은 계속 (로컬 큐 버퍼링)
- 인터넷 복귀 시 자동 재시도
- `enable_cloud_push: False`로 설정하면 클라우드 전송 OFF (로컬만)

### Firebase 데이터 구조
```json
{
  "factory": {
    "yooboo":  { "count": 1247, "speed_per_min": 32, "status": "가동중", ... },
    "filling": { "count": 1200, "speed_per_min": 30, "status": "가동중", ... },
    "temp":    { "sensors": [...], "alert": false, ... },
    "metal":   { "status": "preparing" }
  }
}
```

## 🌐 통합 관제 대시보드

- **URL:** `https://obawimzb.gensparkclaw.com/factory` (작업 중)
- **로컬 API (옵션):** 각 라즈베리 IP의 `/api/state` 도 작동 (기존 호환)

## 🔧 설정 변경

`config.json` 파일을 만들어서 설정 오버라이드 가능 (선택 사항):
```bash
cp config.json.example config.json
nano config.json
```

또는 각 .py 파일 상단의 `CONFIG = {...}` 직접 수정.

## 🐛 문제 해결

| 증상 | 해결 |
|------|------|
| 카메라 안 열림 | `camera_index` 값 0↔1 변경 |
| 카운트 너무 많음 | `min_area` 값 늘리기 |
| 카운트 안 됨 | `line_position` 값 조정, 조명 확인 |
| 클라우드 PUSH 실패 | `journalctl -u heyfood-yooboo -f` 로그 확인 |
| Firebase 401 오류 | 영민님께 보안 규칙 확인 부탁 |

## 📊 모니터링

각 서비스 상태:
```bash
sudo systemctl status heyfood-yooboo
sudo systemctl status heyfood-filling
sudo systemctl status heyfood-temp

# 실시간 로그
sudo journalctl -u heyfood-yooboo -f
```

## 📜 변경 이력

- **2026-05-14**: 클라우드 전환 (Firebase Realtime DB)
  - `cloud_push.py` 추가 → 5초마다 자동 PUSH
  - 4개 .py 파일에 CloudPusher 통합
  - autostart.sh를 장비별 systemd 서비스 등록 방식으로 개편
  - 인터넷 끊겨도 로컬 카운팅 유지
- **2026-05-13**: counter2.py, temperature.py, metal_detector.py 추가 (영민님)
- **2026-05-(이전)**: counter.py 초기 버전 (영민님)

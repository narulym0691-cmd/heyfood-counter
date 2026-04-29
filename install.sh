#!/bin/bash
# 헤이푸드 카운터 설치 스크립트 (라즈베리파이용)
# 실행: bash install.sh

echo "======================================"
echo " 헤이푸드 생산 카운터 설치 시작"
echo "======================================"

# 시스템 패키지 업데이트
sudo apt update -y

# OpenCV 및 Python 의존성 설치
sudo apt install -y python3-pip python3-opencv libatlas-base-dev

# Python 패키지 설치
pip3 install flask flask-cors

echo ""
echo "======================================"
echo " 설치 완료!"
echo " 실행 방법: python3 counter.py"
echo " 대시보드:  http://라즈베리파이IP:5000"
echo "======================================"

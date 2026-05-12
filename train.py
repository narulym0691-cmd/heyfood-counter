#!/usr/bin/env python3
"""
헤이푸드 유부초밥 불량 검출 모델 학습 스크립트
MobileNetV2 Transfer Learning 사용

사전 준비:
  data/정상/  ← 정상 유부초밥 사진 (최소 50장 권장)
  data/불량/  ← 불량 유부초밥 사진 (최소 50장 권장)

실행:
  python3 train.py

결과:
  model/defect_model.h5      ← Keras 모델
  model/defect_model.tflite  ← TensorFlow Lite 모델 (라즈베리파이용)
"""

import os
import sys
import numpy as np

# ─────────────────────────────────────────────
# 설정값
# ─────────────────────────────────────────────
CONFIG = {
    "img_size": (224, 224),       # MobileNetV2 입력 크기
    "batch_size": 16,             # 라즈베리파이 메모리 고려
    "epochs": 20,                 # 학습 에폭 수
    "learning_rate": 1e-4,        # 학습률
    "validation_split": 0.2,     # 검증 데이터 비율
    "data_dir": "data",           # 학습 데이터 폴더
    "model_dir": "model",         # 모델 저장 폴더
    "model_h5": "model/defect_model.h5",
    "model_tflite": "model/defect_model.tflite",
    "class_names": ["정상", "불량"],  # 클래스 이름 (알파벳 순서 아닌 폴더 순서)
}


def check_data():
    """학습 데이터 폴더 및 이미지 수 확인"""
    normal_dir = os.path.join(CONFIG["data_dir"], "정상")
    defect_dir = os.path.join(CONFIG["data_dir"], "불량")

    if not os.path.exists(normal_dir):
        print(f"[ERROR] 폴더 없음: {normal_dir}")
        print("  → python3 train.py 실행 전 data/정상/ 폴더에 정상 사진을 넣어주세요")
        sys.exit(1)

    if not os.path.exists(defect_dir):
        print(f"[ERROR] 폴더 없음: {defect_dir}")
        print("  → python3 train.py 실행 전 data/불량/ 폴더에 불량 사진을 넣어주세요")
        sys.exit(1)

    exts = (".jpg", ".jpeg", ".png", ".bmp")
    normal_count = sum(1 for f in os.listdir(normal_dir) if f.lower().endswith(exts))
    defect_count = sum(1 for f in os.listdir(defect_dir) if f.lower().endswith(exts))

    print(f"[INFO] 정상 이미지: {normal_count}장")
    print(f"[INFO] 불량 이미지: {defect_count}장")

    if normal_count < 10 or defect_count < 10:
        print("[WARN] 이미지가 매우 적습니다. 각 클래스당 최소 50장 이상 권장합니다.")

    if normal_count == 0 or defect_count == 0:
        print("[ERROR] 이미지가 없습니다. 학습을 중단합니다.")
        sys.exit(1)

    return normal_count, defect_count


def build_model():
    """MobileNetV2 기반 이진 분류 모델 구성"""
    import tensorflow as tf
    from tensorflow.keras import layers, models
    from tensorflow.keras.applications import MobileNetV2

    print("[INFO] MobileNetV2 기반 모델 구성 중...")

    # 사전 학습된 MobileNetV2 (ImageNet 가중치, 최상위 분류기 제외)
    base_model = MobileNetV2(
        input_shape=(*CONFIG["img_size"], 3),
        include_top=False,
        weights="imagenet"
    )
    # 초기에는 기반 모델 동결 (Transfer Learning 1단계)
    base_model.trainable = False

    # 분류기 레이어 추가
    model = models.Sequential([
        base_model,
        layers.GlobalAveragePooling2D(),
        layers.BatchNormalization(),
        layers.Dense(128, activation="relu"),
        layers.Dropout(0.3),
        layers.Dense(64, activation="relu"),
        layers.Dropout(0.2),
        layers.Dense(1, activation="sigmoid"),  # 이진 분류 (정상=0, 불량=1)
    ])

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=CONFIG["learning_rate"]),
        loss="binary_crossentropy",
        metrics=["accuracy"]
    )

    print(model.summary())
    return model, base_model


def create_data_generators():
    """데이터 증강 및 제너레이터 생성"""
    from tensorflow.keras.preprocessing.image import ImageDataGenerator

    # 학습용 데이터 증강 (과적합 방지)
    train_datagen = ImageDataGenerator(
        rescale=1.0 / 255.0,           # 픽셀값 정규화 [0,1]
        rotation_range=15,              # ±15도 회전
        width_shift_range=0.1,          # 좌우 이동
        height_shift_range=0.1,         # 상하 이동
        brightness_range=[0.7, 1.3],    # 밝기 변화 (조명 변화 대응)
        horizontal_flip=True,           # 좌우 반전
        vertical_flip=False,            # 상하 반전 (유부초밥 특성상 비활성)
        zoom_range=0.1,                 # 확대/축소
        fill_mode="nearest",
        validation_split=CONFIG["validation_split"],
    )

    # 검증용 (증강 없이 정규화만)
    val_datagen = ImageDataGenerator(
        rescale=1.0 / 255.0,
        validation_split=CONFIG["validation_split"],
    )

    # classes 순서: ["불량", "정상"] → 알파벳 기준 자동 정렬됨
    # binary 분류에서 첫 번째 클래스=0, 두 번째=1
    train_gen = train_datagen.flow_from_directory(
        CONFIG["data_dir"],
        target_size=CONFIG["img_size"],
        batch_size=CONFIG["batch_size"],
        class_mode="binary",
        subset="training",
        shuffle=True,
    )

    val_gen = val_datagen.flow_from_directory(
        CONFIG["data_dir"],
        target_size=CONFIG["img_size"],
        batch_size=CONFIG["batch_size"],
        class_mode="binary",
        subset="validation",
        shuffle=False,
    )

    print(f"[INFO] 클래스 인덱스: {train_gen.class_indices}")
    print(f"[INFO] 학습 샘플: {train_gen.samples}장 | 검증 샘플: {val_gen.samples}장")

    return train_gen, val_gen, train_gen.class_indices


def train(model, base_model, train_gen, val_gen):
    """모델 학습 (2단계: 동결 학습 → Fine-tuning)"""
    import tensorflow as tf

    os.makedirs(CONFIG["model_dir"], exist_ok=True)

    # 콜백 설정
    callbacks = [
        tf.keras.callbacks.ModelCheckpoint(
            CONFIG["model_h5"],
            monitor="val_accuracy",
            save_best_only=True,
            verbose=1,
        ),
        tf.keras.callbacks.EarlyStopping(
            monitor="val_accuracy",
            patience=5,
            restore_best_weights=True,
            verbose=1,
        ),
        tf.keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss",
            factor=0.5,
            patience=3,
            min_lr=1e-7,
            verbose=1,
        ),
    ]

    # ── 1단계: 기반 모델 동결, 분류기만 학습 ──
    print("\n[TRAIN] 1단계: 분류기 레이어 학습 (기반 모델 동결)")
    history1 = model.fit(
        train_gen,
        validation_data=val_gen,
        epochs=CONFIG["epochs"] // 2,
        callbacks=callbacks,
        verbose=1,
    )

    # ── 2단계: 기반 모델 일부 해동, Fine-tuning ──
    print("\n[TRAIN] 2단계: Fine-tuning (MobileNetV2 상위 30개 레이어 해동)")
    base_model.trainable = True
    # 하위 레이어는 유지, 상위 레이어만 학습
    fine_tune_at = len(base_model.layers) - 30
    for layer in base_model.layers[:fine_tune_at]:
        layer.trainable = False

    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=CONFIG["learning_rate"] / 10),
        loss="binary_crossentropy",
        metrics=["accuracy"],
    )

    history2 = model.fit(
        train_gen,
        validation_data=val_gen,
        epochs=CONFIG["epochs"],
        initial_epoch=history1.epoch[-1] + 1,
        callbacks=callbacks,
        verbose=1,
    )

    return history1, history2


def print_results(history1, history2):
    """학습 결과 출력"""
    # 두 history 합치기
    all_acc = history1.history.get("accuracy", []) + history2.history.get("accuracy", [])
    all_val_acc = history1.history.get("val_accuracy", []) + history2.history.get("val_accuracy", [])
    all_loss = history1.history.get("loss", []) + history2.history.get("loss", [])
    all_val_loss = history1.history.get("val_loss", []) + history2.history.get("val_loss", [])

    best_val_acc = max(all_val_acc) if all_val_acc else 0
    best_epoch = all_val_acc.index(best_val_acc) + 1 if all_val_acc else 0

    print("\n" + "=" * 50)
    print("  학습 완료 결과")
    print("=" * 50)
    print(f"  총 학습 에폭:       {len(all_acc)}")
    print(f"  최고 검증 정확도:   {best_val_acc * 100:.2f}%  (에폭 {best_epoch})")
    print(f"  최종 학습 정확도:   {all_acc[-1] * 100:.2f}%")
    print(f"  최종 검증 정확도:   {all_val_acc[-1] * 100:.2f}%")
    print(f"  최종 학습 손실:     {all_loss[-1]:.4f}")
    print(f"  최종 검증 손실:     {all_val_loss[-1]:.4f}")
    print(f"\n  모델 저장 위치: {CONFIG['model_h5']}")
    print("=" * 50)

    if best_val_acc < 0.85:
        print("\n[WARN] 검증 정확도가 85% 미만입니다.")
        print("  → 학습 데이터를 더 추가하거나 사진 품질을 개선하세요.")
    else:
        print("\n[OK] 모델 성능이 양호합니다. TFLite 변환을 진행합니다...")


def convert_to_tflite():
    """Keras .h5 모델을 TensorFlow Lite .tflite로 변환"""
    import tensorflow as tf

    print(f"\n[CONVERT] {CONFIG['model_h5']} → {CONFIG['model_tflite']}")

    model = tf.keras.models.load_model(CONFIG["model_h5"])

    # TFLite 변환 (양자화 적용으로 모델 크기 축소, 라즈베리파이 성능 향상)
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    converter.optimizations = [tf.lite.Optimize.DEFAULT]  # Dynamic range quantization
    tflite_model = converter.convert()

    with open(CONFIG["model_tflite"], "wb") as f:
        f.write(tflite_model)

    h5_size = os.path.getsize(CONFIG["model_h5"]) / (1024 * 1024)
    tflite_size = os.path.getsize(CONFIG["model_tflite"]) / (1024 * 1024)

    print(f"[CONVERT] 완료!")
    print(f"  H5 모델 크기:     {h5_size:.1f} MB")
    print(f"  TFLite 모델 크기: {tflite_size:.1f} MB (약 {(1 - tflite_size/h5_size)*100:.0f}% 압축)")


def save_class_indices(class_indices):
    """클래스 인덱스를 JSON으로 저장 (추론 시 사용)"""
    import json
    path = os.path.join(CONFIG["model_dir"], "class_indices.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(class_indices, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 클래스 인덱스 저장: {path}")
    print(f"       → {class_indices}")


# ─────────────────────────────────────────────
# 메인 실행
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  헤이푸드 유부초밥 불량 검출 모델 학습")
    print("=" * 50)

    # 1. 데이터 확인
    normal_count, defect_count = check_data()

    # 2. TensorFlow 임포트 (늦게 임포트하여 오류 메시지 개선)
    try:
        import tensorflow as tf
        print(f"[INFO] TensorFlow 버전: {tf.__version__}")
        # GPU 사용 가능 여부 확인 (라즈베리파이는 CPU만)
        gpus = tf.config.list_physical_devices("GPU")
        if gpus:
            print(f"[INFO] GPU 사용: {gpus}")
        else:
            print("[INFO] CPU 모드로 학습합니다 (라즈베리파이 정상)")
    except ImportError:
        print("[ERROR] TensorFlow가 설치되지 않았습니다.")
        print("  → install_ai.sh를 먼저 실행하세요: bash install_ai.sh")
        sys.exit(1)

    # 3. 모델 구성
    model, base_model = build_model()

    # 4. 데이터 제너레이터 생성
    train_gen, val_gen, class_indices = create_data_generators()

    # 5. 클래스 인덱스 저장
    os.makedirs(CONFIG["model_dir"], exist_ok=True)
    save_class_indices(class_indices)

    # 6. 학습
    print(f"\n[TRAIN] 학습 시작 (총 {CONFIG['epochs']}+ 에폭)")
    history1, history2 = train(model, base_model, train_gen, val_gen)

    # 7. 결과 출력
    print_results(history1, history2)

    # 8. TFLite 변환
    convert_to_tflite()

    print("\n[완료] 학습이 완료되었습니다!")
    print(f"  다음 명령으로 불량 검출을 시작하세요:")
    print(f"  python3 ai_defect.py")

# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 全局配置中心
"""
import os
from pathlib import Path

# ==========================================
# 1. 路径与文件系统配置
# ==========================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"          # CSV 数据存储目录
VIDEO_DIR = BASE_DIR / "videos"        # 视频切片存储目录

# 自动确保本地存储目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

# ==========================================
# 2. 硬件外设与串口配置 (树莓派5)
# ==========================================
# 环境传感器 (AIRMOD-X2)
SENSOR_PORT = os.getenv("Y2H_SENSOR_PORT", "/dev/ttyAMA0")
SENSOR_BAUD = int(os.getenv("Y2H_SENSOR_BAUD", 9600))

# GPS 模块
GPS_PORT = os.getenv("Y2H_GPS_PORT", "/dev/ttyAMA4")
GPS_BAUD = int(os.getenv("Y2H_GPS_BAUD", 9600))

# UPS 电池 I2C 配置 (Waveshare HAT 常用地址)
UPS_I2C_BUS = int(os.getenv("Y2H_UPS_BUS", 1))
UPS_I2C_ADDR = int(os.getenv("Y2H_UPS_ADDR", 0x42))

# ==========================================
# 3. 摄像头与视频流配置
# ==========================================
CAMERA_INDEX = int(os.getenv("Y2H_CAMERA_INDEX", 0))    # 默认接第一个USB或CSI相机
VIDEO_WIDTH = int(os.getenv("Y2H_VIDEO_WIDTH", 640))
VIDEO_HEIGHT = int(os.getenv("Y2H_VIDEO_HEIGHT", 480))
VIDEO_FPS = int(os.getenv("Y2H_VIDEO_FPS", 20))
SEGMENT_DURATION = int(os.getenv("Y2H_SEGMENT_DURATION", 3600))  # 视频切片时长(秒)

# ==========================================
# 4. 采样与科研计算参数
# ==========================================
LOG_INTERVAL = float(os.getenv("Y2H_LOG_INTERVAL", 1.0))  # 本地高频写入间隔(秒)

# 传感器科学拉偏校准系数 (Y = A * X + B)
PM25_CAL_A = float(os.getenv("Y2H_PM25_CAL_A", 1.0))
PM25_CAL_B = float(os.getenv("Y2H_PM25_CAL_B", 0.0))

# ==========================================
# 5. 云端服务器通信配置 (新架构核心)
# ==========================================
CLOUD_SERVER_IP = os.getenv("Y2H_CLOUD_IP", "123.45.67.89")  # 替换为你的固定IP服务器
CLOUD_SERVER_PORT = int(os.getenv("Y2H_CLOUD_PORT", 8080))
CLOUD_UPLOAD_INTERVAL = float(os.getenv("Y2H_UPLOAD_INTERVAL", 2.0)) # 推流到云端的间隔(秒)
CLOUD_UPLOAD_ENABLED = os.getenv("Y2H_CLOUD_ENABLE", "True").lower() == "true"

# ==========================================
# 6. 第三方服务配置
# ==========================================
BAIDU_MAP_AK = os.getenv("Y2H_BAIDU_AK", "百度地图AK密钥")

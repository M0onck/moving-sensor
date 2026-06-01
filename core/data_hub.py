# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 核心数据中枢 (Data Hub)
负责在多个硬件采集线程与展示/存储线程之间，提供线程安全的数据交换
"""
import time
import threading
import copy

class DataHub:
    def __init__(self):
        # 核心线程锁，确保同一时刻只有一个线程能修改或读取数据
        self._lock = threading.Lock()
        
        # 内存状态机：存放系统当前最新的一帧数据 (赋初始默认值)
        self._live_data = {
            # 1. 系统与时间戳
            "timestamp": time.time(),
            "time_str": "1970-01-01 00:00:00",
            
            # 2. AIRMOD-X2 环境传感器数据
            "pm25": 0.0,
            "pm10": 0.0,
            "temp": 0.0,
            "rh": 0.0,
            "voc": 0.0,
            "co2": 0.0,
            "sensor_status": "WAITING", # WAITING, ACTIVE, ERROR
            
            # 3. GPS 定位与运动数据
            "lat": 0.000000,
            "lon": 0.000000,
            "speed_kmh": 0.0,
            "altitude": 0.0,
            "satellites": 0,
            "gps_status": "SEARCHING",  # SEARCHING, LOCKED, ERROR
            
            # 4. 设备状态与系统监控
            "battery_pct": 100.0,
            "battery_voltage_v": 0.0,
            "power_status": "未知",
            "cpu_temp": 0.0,
            "disk_free_gb": 0.0,
            "is_recording": False       # 摄像头是否正在录制
        }

    def update_sensor(self, data_dict):
        """供 Sensor 线程调用：更新环境传感器数据"""
        with self._lock:
            # 遍历传入的字典，只更新 _live_data 中存在的键
            for k, v in data_dict.items():
                if k in self._live_data:
                    self._live_data[k] = v
            self._live_data["timestamp"] = time.time()
            self._update_time_str()

    def update_gps(self, data_dict):
        """供 GPS 线程调用：更新位置与速度数据"""
        with self._lock:
            for k, v in data_dict.items():
                if k in self._live_data:
                    self._live_data[k] = v
            self._live_data["timestamp"] = time.time()
            self._update_time_str()

    def update_status(self, data_dict):
        """供 摄像头/系统状态 线程调用：更新电量、CPU等杂项状态"""
        with self._lock:
            for k, v in data_dict.items():
                if k in self._live_data:
                    self._live_data[k] = v

    def get_snapshot(self):
        """
        供 CSV存储 / UI / 云端推流 线程调用：获取当前数据的完整快照。
        注意：必须使用 copy.deepcopy() 返回副本，防止外部意外修改内部字典。
        """
        with self._lock:
            # 拿到锁之后迅速复印一份扔出去，不阻塞硬件线程的写入
            return copy.deepcopy(self._live_data)

    def _update_time_str(self):
        """内部方法：同步更新人类可读的时间字符串"""
        # 注意：调用此方法前，必须确保外部已经持有了 self._lock
        loc_time = time.localtime(self._live_data["timestamp"])
        self._live_data["time_str"] = time.strftime("%Y-%m-%d %H:%M:%S", loc_time)

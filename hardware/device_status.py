# -*- coding: utf-8 -*-
import os
import time
import logging
import shutil
from pathlib import Path
import config

class DeviceStatusWorker:
    def __init__(self, data_hub, stop_event):
        self.data_hub = data_hub
        self.stop_event = stop_event

    def run(self):
        logging.info("设备状态监控线程 [Status-Worker] 已启动")
        while not self.stop_event.is_set():
            try:
                payload = {}
                
                # 1. CPU 温度
                try:
                    temp_path = Path("/sys/class/thermal/thermal_zone0/temp")
                    if temp_path.exists():
                        payload["cpu_temp"] = round(float(temp_path.read_text(errors="ignore").strip()) / 1000, 1)
                except: pass
                    
                # 2. 磁盘空间
                try:
                    payload["disk_free_gb"] = round(shutil.disk_usage(str(config.DATA_DIR)).free / (1024 ** 3), 2)
                except: pass
                    
                # 3. UPS 电池读取 (Waveshare UPS HAT B I2C 0x42)
                battery_info = self._read_ups_i2c()
                if battery_info:
                    payload.update(battery_info)
                    
                self.data_hub.update_status(payload)
            except Exception as e:
                logging.debug(f"读取设备状态失败: {e}")
                
            self.stop_event.wait(5.0)

    def _read_ups_i2c(self):
        try:
            from smbus2 import SMBus
        except ImportError:
            return {}
        
        try:
            with SMBus(config.UPS_I2C_BUS) as bus:
                raw_bus = bus.read_word_data(config.UPS_I2C_ADDR, 0x02)
                raw_bus = ((raw_bus & 0xFF) << 8) | (raw_bus >> 8)
                voltage_v = round(((raw_bus >> 3) * 4) / 1000.0, 2)

                raw_shunt = bus.read_word_data(config.UPS_I2C_ADDR, 0x01)
                raw_shunt = ((raw_shunt & 0xFF) << 8) | (raw_shunt >> 8)
                if raw_shunt & 0x8000:
                    raw_shunt -= 0x10000
                shunt_mv = raw_shunt * 0.01

                if not (3.0 <= voltage_v <= 12.8):
                    return {}

                pct = round((voltage_v - 6.0) / (8.4 - 6.0) * 100, 1)
                pct = max(0.0, min(100.0, pct))
                status = "充电中" if shunt_mv > 0.5 else ("放电中" if shunt_mv < -0.5 else "电池供电")
                
                return {
                    "battery_pct": pct,
                    "battery_voltage_v": voltage_v,
                    "power_status": status
                }
        except Exception:
            return {}

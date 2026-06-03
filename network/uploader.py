# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 边缘网络推送层 (主动上云)
负责从 DataHub 异步提取最新快照，对齐云端 FastAPI 的 Pydantic 规范
"""
import time
import logging
import json
import urllib.request
import urllib.error
from collections import deque
from datetime import datetime

import config

class CloudUploader:
    def __init__(self, data_hub, stop_event):
        self.data_hub = data_hub
        self.stop_event = stop_event
        
        self.server_ip = getattr(config, "CLOUD_SERVER_IP", "123.45.67.89")
        self.server_port = getattr(config, "CLOUD_SERVER_PORT", 8080)
        self.upload_interval = getattr(config, "CLOUD_UPLOAD_INTERVAL", 2.0)
        self.device_id = "pi5_y2h_edge" 
        
        self.api_url = f"http://{self.server_ip}:{self.server_port}/api/upload"
        self.backlog_queue = deque(maxlen=3600)
        
    def run(self):
        logging.info(f"云端主动推流线程 [Cloud-Worker] 已拉起")
        logging.info(f"数据上云目标接口 -> {self.api_url}")
        
        while not self.stop_event.is_set():
            loop_start = time.time()
            try:
                snapshot = self.data_hub.get_snapshot()
                payload = self._prepare_payload(snapshot)
                self.backlog_queue.append(payload)
                self._flush_queue()
                
            except Exception as e:
                logging.error(f"云端推流大循环发生未知异常: {e}")
                
            elapsed = time.time() - loop_start
            sleep_time = max(0.2, self.upload_interval - elapsed)
            self.stop_event.wait(sleep_time)
            
        logging.info("云端推送线程 [Cloud-Worker] 已优雅安全退出。")

    def _to_float(self, val, default=0.0):
        if val is None or val == "-":
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # 【新增】整型数据安全转换，用于卫星数和定位质量
    def _to_int(self, val, default=0):
        if val is None or val == "-":
            return default
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default

    def _prepare_payload(self, raw_data: dict) -> dict:
        timestamp = raw_data.get("time_str")
        if not timestamp or timestamp == "-":
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
        return {
            "timestamp": timestamp,
            "pm25": self._to_float(raw_data.get("pm25")),
            "pm10": self._to_float(raw_data.get("pm10")),
            "latitude": self._to_float(raw_data.get("lat"), 32.060255),
            "longitude": self._to_float(raw_data.get("lon"), 118.796877),
            "speed": self._to_float(raw_data.get("speed_kmh")),
            "temp": self._to_float(raw_data.get("temp")),
            "rh": self._to_float(raw_data.get("rh")),
            "voc": self._to_float(raw_data.get("voc")),
            "co2": self._to_float(raw_data.get("co2")),
            # 【新增】推流 GPS 底层监控数据
            "satellites": self._to_int(raw_data.get("satellites")),
            "fix_quality": self._to_int(raw_data.get("fix_quality")),
            "device_id": self.device_id
        }

    def _flush_queue(self):
        while self.backlog_queue and not self.stop_event.is_set():
            payload = self.backlog_queue[0]
            success = self._send_http_post(payload)
            if success:
                self.backlog_queue.popleft()
            else:
                break

    def _send_http_post(self, payload: dict) -> bool:
        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.api_url,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    return True
            return False
            
        except urllib.error.URLError as e:
            logging.debug(f"云端服务器暂时不可达: {e}")
            return False
        except Exception as e:
            logging.error(f"主动推送云服务器出错: {e}")
            return False

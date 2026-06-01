# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 边缘网络推送层 (主动上云)
负责从 DataHub 异步提取最新快照，对齐云端 FastAPI 的 Pydantic 规范，支持网络波动断线重连与数据自动补发
"""
import time
import logging
import json
import urllib.request
import urllib.error
from collections import deque

import config

class CloudUploader:
    def __init__(self, data_hub, stop_event):
        """
        初始化云端推流模块
        :param data_hub: 线程安全的数据中枢实例
        :param stop_event: 全局停止事件锁
        """
        self.data_hub = data_hub
        self.stop_event = stop_event
        
        # 从配置中心动态读取网络参数
        self.server_ip = getattr(config, "CLOUD_SERVER_IP", "123.45.67.89")
        self.server_port = getattr(config, "CLOUD_SERVER_PORT", 8080)
        self.upload_interval = getattr(config, "CLOUD_UPLOAD_INTERVAL", 2.0)
        self.device_id = "pi5_y2h_edge" # 本设备的唯一ID标识
        
        # 组装完整的 FastAPI 路由地址
        self.api_url = f"http://{self.server_ip}:{self.server_port}/api/upload"
        
        # 断网容错重试缓冲区（最大保留3600条，约等于断网1小时的数据缓冲，防止撑爆内存）
        self.backlog_queue = deque(maxlen=3600)
        
    def run(self):
        """线程执行主入口"""
        logging.info(f"云端主动推流线程 [Cloud-Worker] 已拉起")
        logging.info(f"数据上云目标接口 -> {self.api_url}")
        
        while not self.stop_event.is_set():
            loop_start = time.time()
            try:
                # 1. 抓取边缘端当前最新的一帧数据快照
                snapshot = self.data_hub.get_snapshot()
                
                # 2. 将 DataHub 的短键名映射对齐到云端 sqlite 期待的 Schema 上
                payload = self._map_to_cloud_schema(snapshot)
                
                # 3. 将新捕获的数据送入重试队列
                self.backlog_queue.append(payload)
                
                # 4. 尝试排空重试队列（如果网络正常，会一次性发完所有积压数据）
                self._flush_queue()
                
            except Exception as e:
                logging.error(f"云端推流大循环发生未知异常: {e}")
                
            # 计算精确时间片对齐
            elapsed = time.time() - loop_start
            sleep_time = max(0.2, self.upload_interval - elapsed)
            self.stop_event.wait(sleep_time)
            
        logging.info("云端推送线程 [Cloud-Worker] 已优雅安全退出。")

    def _map_to_cloud_schema(self, snapshot: dict) -> dict:
        """
        将 DataHub 的底层短格式映射转换为云服务器上 fastapi (SensorData) 期待的结构
        """
        # DataHub 默认采用 time_str，如果为空则拼装当前时间
        timestamp = snapshot.get("time_str")
        if not timestamp or timestamp == "-":
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
        return {
            "timestamp": timestamp,
            "pm25": self._to_float_or_none(snapshot.get("pm25")),
            "pm10": self._to_float_or_none(snapshot.get("pm10")),
            "latitude": self._to_float_or_none(snapshot.get("lat")),
            "longitude": self._to_float_or_none(snapshot.get("lon")),
            "speed": self._to_float_or_none(snapshot.get("speed_kmh")),
            "device_id": self.device_id
        }

    def _to_float_or_none(self, val):
        """类型安全转换器"""
        if val is None or val == "-":
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    def _flush_queue(self):
        """排空待发缓冲区并进行网络请求提交"""
        while self.backlog_queue and not self.stop_event.is_set():
            # 拿出最老的一条
            payload = self.backlog_queue[0]
            
            # 执行推送
            success = self._send_http_post(payload)
            
            if success:
                # 推送成功，安全弹出
                self.backlog_queue.popleft()
            else:
                # 一旦单条失败，说明网络依然卡顿或云端离线，不继续发送后面的数据
                # 保持原队列不动，退出，留待下一个循环周期重试
                break

    def _prepare_payload(self, raw_data: dict) -> dict:
        """数据适配器：将本地 DataHub 字段转换为云端 FastAPI 协议字段"""
        return {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pm25": raw_data.get("pm25", 0),
            "pm10": raw_data.get("pm10", 0),
            "latitude": raw_data.get("lat", 0.0),      # 映射: lat -> latitude
            "longitude": raw_data.get("lon", 0.0),     # 映射: lon -> longitude
            "speed": raw_data.get("speed_kmh", 0.0),
            "temp": raw_data.get("temp", 0.0),
            "rh": raw_data.get("rh", 0.0),
            "voc": raw_data.get("voc", 0),
            "co2": raw_data.get("co2", 0),
            "device_id": self.device_id
        }

    def _flush_queue(self):
        """修改后的排空逻辑"""
        while self.backlog_queue and not self.stop_event.is_set():
            payload = self.backlog_queue[0]
            # 在发送前先转换协议
            mapped_payload = self._prepare_payload(payload)
            success = self._send_http_post(mapped_payload)
            if success:
                self.backlog_queue.popleft()
            else:
                break

    def _send_http_post(self, payload: dict) -> bool:
        """
        利用纯 Python 原生 urllib 发起高性能 POST 报文提交，不给树莓派增加第三方库依赖负担
        """
        try:
            data_bytes = json.dumps(payload).encode("utf-8")
            
            req = urllib.request.Request(
                self.api_url,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            
            # 设置极快的超时（3秒），保证卡顿情况下不会因为网络死锁阻塞整个树莓派线程
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    return True
            return False
            
        except urllib.error.URLError as e:
            # 仅记录到 debug 层级，避免网络正常断线时，树莓派日志不断刷屏爆满
            logging.debug(f"云端服务器暂时不可达 (网络断开或防火墙封锁): {e}")
            return False
        except Exception as e:
            logging.error(f"主动推送云服务器出错: {e}")
            return False

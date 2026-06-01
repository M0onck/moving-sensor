# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 主程序总入口
"""
import sys
import time
import threading
import signal
import logging

# 导入配置项
import config

# 导入即将解耦构建的各个业务子模块
from core.data_hub import DataHub
from core.storage import DataStorageWorker
from hardware.sensor import SensorWorker
from hardware.gps import GPSWorker
from hardware.camera import CameraWorker
from ui.local_window import LocalUI
# from web.uploader import CloudUploader # 预留给后续开发的云端推流模块

# 全局日志格式配置
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(threadName)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

class Y2HSystem:
    def __init__(self):
        # 初始化全局线程停止信号（当事件 set 时，所有子线程必须安全退出循环）
        self.stop_event = threading.Event()
        
        # 初始化线程安全的内存数据中枢 (Data Hub)
        self.data_hub = DataHub()
        
        # 存储活动的后台线程句柄
        self.threads = []
        self.ui = None

    def start(self):
        logging.info("==============================================")
        logging.info("正在启动 Y2H 移动环境感知与走航监测系统 (树莓派5)...")
        logging.info("==============================================")
        
        # 1. 定义并实例化各个解耦后的异步工作线程任务
        workers = {
            "Sensor-Worker": SensorWorker(self.data_hub, self.stop_event),
            "GPS-Worker": GPSWorker(self.data_hub, self.stop_event),
            "Camera-Worker": CameraWorker(self.data_hub, self.stop_event),
            "Storage-Worker": DataStorageWorker(self.data_hub, self.stop_event)
        }
        
        # 如果配置启用了云端推流，则动态加入推流线程
        if config.CLOUD_UPLOAD_ENABLED:
            # workers["Cloud-Worker"] = CloudUploader(self.data_hub, self.stop_event)
            logging.info(f"已激活云端实时推流目标 -> {config.CLOUD_SERVER_IP}:{config.CLOUD_SERVER_PORT}")

        # 2. 批量以守护线程(Daemon)模式拉起后台工作
        for name, worker in workers.items():
            t = threading.Thread(target=worker.run, name=name, daemon=True)
            t.start()
            self.threads.append(t)
            logging.info(f"成功拉起子系统线程: [{name}]")

        # 3. 唤醒本地展示大屏 (Tkinter UI 机制要求必须驻留在系统 UI 主线程中)
        logging.info("后台工作准备就绪，正在初始化本地 GUI 渲染界面...")
        self.ui = LocalUI(self.data_hub, self.stop_event)
        
        # 此行会发生阻塞，直到用户手动关闭树莓派屏幕上的 Tkinter 窗口
        self.ui.show()
        
        # 当窗口被关闭后，代码自然向下流转，触发系统整体安全清场
        self.shutdown()

    def shutdown(self):
        """安全收尾与清场函数，防止死锁或文件损坏"""
        if self.stop_event.is_set():
            return
            
        logging.warning("监控到系统关闭请求，正在向所有子线程下发安全断电信号...")
        self.stop_event.set()  # 广播停止信号
        
        # 优雅等待后台子线程释放硬件端口、关闭文件句柄
        for t in self.threads:
            t.join(timeout=2.0)
            logging.info(f"线程 [{t.name}] 已安全释放资源并注销。")
            
        logging.info("所有边缘端外设与数据流已清场完毕。Y2H 系统安全退出。")
        sys.exit(0)

if __name__ == "__main__":
    system = Y2HSystem()
    
    # 接管 Linux 系统的退出信号（如 终端按 Ctrl+C，或者通过系统 kill 进程）
    def signal_handler(sig, frame):
        logging.warning(f"捕获到系统级别中断信号 (Signal: {sig})")
        system.shutdown()
        
    signal.signal(signal.SIGINT, signal_handler)   # 捕获 Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # 捕获 kill
    
    # 挂载总控
    system.start()

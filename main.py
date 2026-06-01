# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 主程序总入口 (支持本地显示器自动环境探查)
"""
import sys
import time
import threading
import signal
import logging
import os

# 导入配置项
import config

# 导入解耦构建的各个业务子模块
from core.data_hub import DataHub
from core.storage import DataStorageWorker
from hardware.sensor import SensorWorker
from hardware.gps import GPSWorker
from hardware.camera import CameraWorker
from network.uploader import CloudUploader
from hardware.device_status import DeviceStatusWorker

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
        self.data_hub = data_hub = DataHub()
        
        # 存储活动的后台线程句柄
        self.threads = []
        self.ui = None

    def _is_display_environment_available(self) -> bool:
        """
        核心智能探查算法：
        主动扫描 Linux 底层是否存在可连接的物理桌面显示端口，以此判定是否拉起 Tkinter 看板
        """
        # 检查 X11 的 DISPLAY 环境变量是否存在
        if os.getenv("DISPLAY"):
            return True
            
        # 检查新版树莓派5常用的 Wayland 桌面会话标志
        if os.getenv("WAYLAND_DISPLAY"):
            return True
            
        # 针对有些桌面环境已经开启但通过 SSH 会话进入的情况，尝试通过系统管道临时检测物理桌面 X11 连接
        try:
            # 尝试导入 tkinter 并建立一次虚拟物理握手
            import tkinter as tk
            root = tk.Tk()
            root.destroy()
            return True
        except Exception:
            # 如果抛出 TclError 说明当前连接中没有任何可以使用的屏幕画布
            return False

    def start(self):
        logging.info("==============================================")
        logging.info("正在启动 Y2H 移动环境感知与走航监测系统 (树莓派5)...")
        logging.info("==============================================")
        
        # 1. 实例化各个工作子模块
        workers = {
            "Sensor-Worker": SensorWorker(self.data_hub, self.stop_event),
            "GPS-Worker": GPSWorker(self.data_hub, self.stop_event),
            "Storage-Worker": DataStorageWorker(self.data_hub, self.stop_event),
            "Status-Worker": DeviceStatusWorker(self.data_hub, self.stop_event)
        }
        
        # 视配置状态拉起摄像头子线程
        camera_worker = None
        if config.CAMERA_ENABLED:
            camera_worker = CameraWorker(self.data_hub, self.stop_event)
            workers["Camera-Worker"] = camera_worker
        else:
            logging.info("由于 [config.CAMERA_ENABLED = False]，摄像头数据采集已被关闭。")
        
        # 如果配置启用了云端推流，则动态加入推流线程
        if config.CLOUD_UPLOAD_ENABLED:
            workers["Cloud-Worker"] = CloudUploader(self.data_hub, self.stop_event)
            logging.info(f"已激活云端实时推流目标 -> {config.CLOUD_SERVER_IP}:{config.CLOUD_SERVER_PORT}")

        # 2. 批量以守护线程(Daemon)模式拉起后台工作
        for name, worker in workers.items():
            t = threading.Thread(target=worker.run, name=name, daemon=True)
            t.start()
            self.threads.append(t)
            logging.info(f"成功拉起子系统线程: [{name}]")

        # 3. 展现层决策 (智能自动感知探查，不依赖写死配置开关)
        # 优先读取 config 里的配置。如果 config 允许，我们就主动自检当前屏幕是否可达。
        if config.LIVE_WINDOW_ENABLED or self._is_display_environment_available():
            logging.info("系统智能判定：本地物理显示屏 [已就绪]。正在唤醒本地大屏图形渲染引擎...")
            try:
                # 临时给当前的进程环境指派 DISPLAY :0 环境变量以提高拉起成功率
                if not os.getenv("DISPLAY") and not os.getenv("WAYLAND_DISPLAY"):
                    os.environ["DISPLAY"] = ":0"
                    
                from ui.local_window import LocalUI
                self.ui = LocalUI(self.data_hub, self.stop_event, camera_worker)
                self.ui.show()  # 阻塞主线程
                self.shutdown()
            except Exception as e:
                logging.error(f"本地大屏图形引擎启动失败: {e}。系统将自动降级为 [后台无头盲采模式 (Headless Mode)] 运行...")
                self._headless_block()
        else:
            logging.info("系统智能判定：本地屏幕不可达。已自动切换为 [高效后台无头采集模式 (Headless Mode)]。")
            self._headless_block()

    def _headless_block(self):
        """无图形显示场景下的非阻塞守护锁，替代 Tkinter 的 mainloop 阻塞主进程"""
        while not self.stop_event.is_set():
            try:
                time.sleep(1.0)
            except (KeyboardInterrupt, SystemExit):
                break
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

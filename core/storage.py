# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 核心数据持久化层
负责按固定频率从 DataHub 拉取数据快照，并安全、防断电地写入本地 CSV 文件
"""
import os
import time
import csv
import logging
from datetime import datetime

import config

class DataStorageWorker:
    def __init__(self, data_hub, stop_event):
        """
        初始化数据存储工人类
        :param data_hub: 线程安全的数据中枢实例
        :param stop_event: 全局停止事件锁
        """
        self.data_hub = data_hub
        self.stop_event = stop_event
        self.interval = config.LOG_INTERVAL
        
        # 确保数据存储目录存在
        self.data_dir = config.DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # 按系统启动时间生成唯一的数据文件名
        start_time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.csv_filename = self.data_dir / f"y2h_data_{start_time_str}.csv"
        
        # 定义 CSV 列名 (必须与 DataHub 中快照的字段对应)
        self.fieldnames = [
            "time_str", "timestamp",
            "lat", "lon", "speed_kmh", "altitude", "satellites", "gps_status",
            "pm25", "pm10", "temp", "rh", "voc", "co2", "sensor_status",
            "battery_pct", "cpu_temp", "disk_free_gb", "is_recording"
        ]

    def run(self):
        """线程执行入口"""
        logging.info(f"数据存储线程 [Storage-Worker] 已启动. 写入间隔: {self.interval}s")
        logging.info(f"本次运行数据将保存在: {self.csv_filename}")
        
        try:
            # 使用追加模式(a)打开文件，newline='' 防止 Windows/Linux 下出现空行
            with open(self.csv_filename, mode='a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames, extrasaction='ignore')
                
                # 如果是新文件，先写入表头
                if f.tell() == 0:
                    writer.writeheader()
                    f.flush()
                    os.fsync(f.fileno())  # 强制刷入磁盘扇区
                
                # 进入高频写入循环
                while not self.stop_event.is_set():
                    loop_start = time.time()
                    
                    try:
                        # 1. 从数据中枢获取最新一帧的安全快照
                        snapshot = self.data_hub.get_snapshot()
                        
                        # 2. 写入 CSV 缓冲区
                        writer.writerow(snapshot)
                        
                        # 3. 极其重要的防断电操作：强制要求操作系统把缓冲区的内容刷进 SD 卡物理层
                        f.flush()
                        os.fsync(f.fileno())
                        
                    except IOError as e:
                        logging.error(f"写入 CSV 文件时发生 IO 异常: {e}")
                    except Exception as e:
                        logging.error(f"存储线程处理数据时发生未知异常: {e}")
                        
                    # 精确控制采集频率，扣除写入耗时
                    elapsed = time.time() - loop_start
                    sleep_time = max(0.1, self.interval - elapsed)
                    
                    # 响应全局停止信号的休眠
                    self.stop_event.wait(sleep_time)
                    
        except PermissionError:
            logging.error(f"无法创建或打开数据文件 {self.csv_filename}，请检查目录权限！")
        except Exception as e:
            logging.error(f"存储线程发生致命错误: {e}")
            
        logging.info("数据存储线程 [Storage-Worker] 已安全关闭，所有文件句柄已释放。")

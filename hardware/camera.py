# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 硬件驱动层：摄像头捕获与视频切片录制模块
支持在没有接入摄像头时自适应降级为“仿真视频源”，防止底层 C++ 报错打断 Python 进程，且支持摄像头热插拔
"""
import os
import time
import logging
from datetime import datetime
from pathlib import Path
import cv2
import numpy as np

import config

class CameraWorker:
    def __init__(self, data_hub, stop_event):
        """
        初始化摄像头工作类
        :param data_hub: 线程安全的数据中枢实例
        :param stop_event: 全局停止事件
        """
        self.data_hub = data_hub
        self.stop_event = stop_event
        
        # 基础配置项
        self.camera_index = config.CAMERA_INDEX
        self.width = config.VIDEO_WIDTH
        self.height = config.VIDEO_HEIGHT
        self.fps = config.VIDEO_FPS
        self.segment_duration = config.SEGMENT_DURATION
        self.video_dir = config.VIDEO_DIR
        
        # 核心设备与状态标志位
        self.cap = None
        self.out = None
        self.current_video_path = None
        self.segment_start_time = 0
        self.is_mock = True  # 默认在未检测到硬件时采用 Mock 状态
        
        # 缓存图像，供 UI 线程共享
        self.last_frame = None

    def _check_system_v4l_devices(self) -> bool:
        """
        通过 Linux 虚拟文件系统主动探查有无挂载物理 video 捕捉设备。
        这是防止 C++ 内核越界崩溃的最重要前置机制。
        """
        try:
            video_dir = Path("/sys/class/video4linux")
            if not video_dir.exists():
                return False
                
            devices = list(video_dir.glob("video*"))
            # 必须确实有设备挂载
            if len(devices) > 0:
                return True
        except Exception as e:
            logging.debug(f"检查 video4linux 时异常: {e}")
        return False

    def run(self):
        """线程主执行入口"""
        logging.info("视频录制线程 [Camera-Worker] 已拉起。")
        
        while not self.stop_event.is_set():
            try:
                # 1. 物理检查：判断目前树莓派上是否有可用硬件
                has_hardware = self._check_system_v4l_devices()
                
                if not has_hardware:
                    if not self.is_mock:
                        logging.warning("监控到物理摄像头被拔出或挂载丢失！安全热切回 [仿真视频模式]...")
                        self.is_mock = True
                        self._close_camera()
                else:
                    # 如果有硬件，且目前还在 mock 模式，说明可能是刚刚插入了物理摄像头，尝试热拔插初始化
                    if self.is_mock:
                        logging.info("探查到系统中有物理摄像头插接，尝试初始化物理硬件驱动...")
                        self._init_physical_camera()

                # 2. 核心大循环流处理
                if self.is_mock:
                    self._mock_stream_loop()
                else:
                    self._physical_stream_loop()
                    
            except Exception as e:
                logging.error(f"摄像头处理线程遭遇未知异常: {e}，5秒后重启恢复...")
                self._close_all()
                self.stop_event.wait(5.0)

        # 线程注销前彻底清除
        self._close_all()
        logging.info("视频录制线程 [Camera-Worker] 已优雅安全注销。")

    def _init_physical_camera(self):
        """极力防崩溃地初始化 OpenCV VideoCapture 驱动"""
        self._close_camera()
        try:
            # 在树莓派 5 上，建议使用 V4L2 硬件编解码后端
            self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_V4L2)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            
            # 部分设备由于参数不支持导致 open 假死，需要再次判定 isOpened
            if not self.cap.isOpened():
                logging.warning(f"由于摄像头打开未就绪 (Index: {self.camera_index})，降级保持 [仿真模式]")
                self.is_mock = True
                self._close_camera()
            else:
                logging.info(f"成功与物理摄像头建立握手连接。当前索引: {self.camera_index}")
                self.is_mock = False
        except Exception as err:
            logging.error(f"OpenCV 底层 C++ 接口初始化异常拦截: {err}")
            self.is_mock = True
            self._close_camera()

    def _physical_stream_loop(self):
        """物理捕获循环。一旦读取失败，优雅切回 mock，不中断 Python 运行"""
        frame_interval = 1.0 / self.fps
        
        while not self.stop_event.is_set() and self.cap and self.cap.isOpened():
            start_time = time.time()
            try:
                ret, frame = self.cap.read()
                if not ret or frame is None:
                    logging.warning("摄像头物理采帧为空。可能线路连接不良，将安全折返 Mock...")
                    break
                    
                # 图像大小归一化
                if frame.shape[1] != self.width or frame.shape[0] != self.height:
                    frame = cv2.resize(frame, (self.width, self.height))
                
                # 写入视频切片
                self._handle_video_segmentation(frame)
                self.data_hub.update_status({"is_recording": True})
                self._share_frame_to_hub(frame)
                
            except Exception as err:
                logging.error(f"物理抓帧总线驱动异常: {err}")
                break
            
            elapsed = time.time() - start_time
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)
            
        # 若退出说明硬件可能发生松动断连，重置状态
        self.is_mock = True
        self._close_camera()

    def _mock_stream_loop(self):
        """
        高度优化的图形仿真循环：
        提供一条带炫酷雷达扫描动画以及传感器物理字段实时文字贴片的高质量仿真数据流
        """
        frame_interval = 1.0 / self.fps
        angle = 0
        
        # 为防止在 mock 模式下高频检测浪费 CPU，设定一个探测计时器
        last_hw_check = time.time()
        
        while not self.stop_event.is_set():
            start_time = time.time()
            
            # 每隔 15 秒主动到后台探查是否有新的 USB 硬件热插拔进来
            now = time.time()
            if now - last_hw_check > 15.0:
                last_hw_check = now
                if self._check_system_v4l_devices():
                    logging.info("仿真监测器探查到新的 USB 视频设备热插拔，打破仿真，准备切入硬件读取...")
                    break

            # 1. 绘制仿真大底看板 (640x480 RGB 帧)
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            
            # 背景画一些深蓝网格线，凸显科技感
            for y in range(0, self.height, 40):
                cv2.line(frame, (0, y), (self.width, y), (20, 24, 33), 1)
            for x in range(0, self.width, 40):
                cv2.line(frame, (x, 0), (x, self.height), (20, 24, 33), 1)
                
            center_x, center_y = self.width // 2, self.height // 2
            
            # 画一个圆规探测扫描星盘
            cv2.circle(frame, (center_x, center_y), 90, (15, 23, 42), -1)
            cv2.circle(frame, (center_x, center_y), 90, (30, 41, 59), 2)
            cv2.circle(frame, (center_x, center_y), 45, (30, 41, 59), 1)
            
            angle = (angle + 5) % 360
            rad = np.deg2rad(angle)
            end_x = int(center_x + 90 * np.cos(rad))
            end_y = int(center_y + 90 * np.sin(rad))
            cv2.line(frame, (center_x, center_y), (end_x, end_y), (34, 197, 94), 2)
            
            # 2. 实时从数据中枢提取传感器最新的值叠加在视频上
            # 这样哪怕没有物理摄像头，生成的 MP4 视频切片里也带有丰富的历史数据曲线
            d = self.data_hub.get_snapshot()
            curr_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            cv2.putText(frame, "Y2H SYSTEM MOCK DIGITAL STREAM", (20, 35), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (248, 250, 252), 2)
            cv2.putText(frame, "[NO CAMERA ATTACHED - SIMULATOR ACTIVE]", (20, 55), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (239, 68, 68), 1)
            
            # OSD 数据面板贴片
            cv2.putText(frame, f"TIME: {curr_time}", (20, 110), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (148, 163, 184), 1)
            cv2.putText(frame, f"PM2.5: {d.get('pm25', '-')} ug/m3", (20, 135), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (249, 115, 22), 1)
            cv2.putText(frame, f"VOC  : {d.get('voc', '-')} ug/m3", (20, 160), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (139, 92, 246), 1)
            cv2.putText(frame, f"CO2  : {d.get('co2', '-')} ppm", (20, 185), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (59, 130, 246), 1)
            cv2.putText(frame, f"GPS  : {d.get('lat', '-')}, {d.get('lon', '-')}", (20, 210), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (6, 182, 212), 1)
            cv2.putText(frame, f"SPEED: {d.get('speed_kmh', '-')} km/h", (20, 235), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (20, 184, 166), 1)

            # 3. 视频切片落盘控制
            self._handle_video_segmentation(frame)
            self.data_hub.update_status({"is_recording": True})
            self._share_frame_to_hub(frame)
            
            elapsed = time.time() - start_time
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)

    def _handle_video_segmentation(self, frame):
        """控制视频文件的分段保存"""
        now = time.time()
        if self.out is None or (now - self.segment_start_time >= self.segment_duration):
            self._close_writer()
            self.video_dir.mkdir(parents=True, exist_ok=True)
            time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.current_video_path = self.video_dir / f"y2h_video_{time_str}.mp4"
            
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self.out = cv2.VideoWriter(
                str(self.current_video_path),
                fourcc,
                self.fps,
                (self.width, self.height)
            )
            self.segment_start_time = now
            logging.info(f"开启新的视频录制切片: {self.current_video_path.name}")
            
        if self.out:
            self.out.write(frame)

    def _share_frame_to_hub(self, frame):
        """将当前帧拷贝至内存浅浅存留，供 UI 绘制快照使用"""
        self.last_frame = frame.copy()

    def get_latest_frame(self):
        """UI 大屏抓图接口"""
        if self.last_frame is not None:
            return self.last_frame
        return None

    def _close_camera(self):
        """安全注销底层 CV2 视频描述句柄"""
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None

    def _close_writer(self):
        """安全刷新缓存并保存视频 MP4 容器尾部"""
        if self.out:
            try:
                self.out.release()
                logging.info(f"已成功保存并封包视频切片: {self.current_video_path.name}")
            except Exception as e:
                logging.error(f"关闭视频写入器失败: {e}")
        self.out = None
        self.current_video_path = None

    def _close_all(self):
        self._close_writer()
        self._close_camera()

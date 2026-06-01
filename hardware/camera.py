# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 硬件驱动层：摄像头捕获与视频切片录制模块
支持 OpenCV 视频帧流捕获、本地大屏共享、按设定时间自动切片以及无摄像头时的 Mock 仿真视频源
"""
import os
import time
import logging
from datetime import datetime
import cv2
import numpy as np

import config

class CameraWorker:
    def __init__(self, data_hub, stop_event):
        """
        初始化摄像头录制与处理类
        :param data_hub: 线程安全的数据中枢实例
        :param stop_event: 全局停止事件锁
        """
        self.data_hub = data_hub
        self.stop_event = stop_event
        
        # 配置参数
        self.camera_index = config.CAMERA_INDEX
        self.width = config.VIDEO_WIDTH
        self.height = config.VIDEO_HEIGHT
        self.fps = config.VIDEO_FPS
        self.segment_duration = config.SEGMENT_DURATION
        self.video_dir = config.VIDEO_DIR
        
        # 内部状态变量
        self.cap = None
        self.out = None
        self.current_video_path = None
        self.segment_start_time = 0
        self.is_mock = False

    def run(self):
        """线程执行入口"""
        logging.info("视频录制线程 [Camera-Worker] 已启动")
        
        while not self.stop_event.is_set():
            try:
                # 1. 尝试初始化 OpenCV 视频捕获设备
                if self.cap is None and not self.is_mock:
                    self.cap = cv2.VideoCapture(self.camera_index)
                    # 设置相机分辨率
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                    
                    # 检查是否成功打开
                    if not self.cap.isOpened():
                        logging.warning(f"未能打开物理摄像头 (Index: {self.camera_index})，将自动启用 [虚拟视频源仿真模式]...")
                        self.is_mock = True
                        self._close_camera()
                    else:
                        logging.info(f"成功打开物理摄像头 (Index: {self.camera_index})")
                        self.is_mock = False
                
                # 2. 根据状态进入相应的视频流循环
                if self.is_mock:
                    self._mock_stream_loop()
                else:
                    self._physical_stream_loop()
                    
            except Exception as e:
                logging.error(f"视频录制线程发生未知异常: {e}，5秒后重试")
                self._close_all()
                self.stop_event.wait(5.0)

        # 线程退出前，释放所有资源
        self._close_all()
        logging.info("视频录制线程 [Camera-Worker] 已优雅安全退出。")

    def _physical_stream_loop(self):
        """物理摄像头数据采集与切片写入循环"""
        # 每帧的目标时间间隔 (控制 FPS)
        frame_interval = 1.0 / self.fps
        
        while not self.stop_event.is_set() and self.cap and self.cap.isOpened():
            start_time = time.time()
            
            # 读取一帧
            ret, frame = self.cap.read()
            if not ret:
                logging.warning("摄像头读取帧失败，可能连接断开")
                break
                
            # 自适应缩放至标准分辨率
            if frame.shape[1] != self.width or frame.shape[0] != self.height:
                frame = cv2.resize(frame, (self.width, self.height))
            
            # 检查并更新切片录制器
            self._handle_video_segmentation(frame)
            
            # 共享最新的一帧画面给 DataHub，供 UI 线程读取显示 (这里直接写入，内部已经解耦)
            # 为了防止多线程冲突和内存拷贝开销，我们通过单独的状态接口进行软关联
            self.data_hub.update_status({"is_recording": True})
            
            # 将最新的图像裸帧保存在内存中，UI 线程可以直接读取
            self._share_frame_to_hub(frame)
            
            # 控制帧率频率
            elapsed = time.time() - start_time
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)
            
        # 若退出循环，则清空状态
        self.data_hub.update_status({"is_recording": False})

    def _mock_stream_loop(self):
        """虚拟摄像头流模拟循环：在没有硬件时绘制动态仪表看板视频"""
        frame_interval = 1.0 / self.fps
        angle = 0
        
        while not self.stop_event.is_set():
            start_time = time.time()
            
            # 创建一个全黑的画布
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            
            # 绘制动态演示图案（雷达扫描和彩色渐变圈），模拟动态视频流
            center_x, center_y = self.width // 2, self.height // 2
            cv2.circle(frame, (center_x, center_y), 80, (50, 50, 50), -1)
            
            # 绘制旋转的扫描线
            angle = (angle + 4) % 360
            rad = np.deg2rad(angle)
            end_x = int(center_x + 80 * np.cos(rad))
            end_y = int(center_y + 80 * np.sin(rad))
            cv2.line(frame, (center_x, center_y), (end_x, end_y), (0, 255, 0), 2)
            
            # 渲染实时的系统虚拟文字
            curr_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cv2.putText(frame, f"Y2H SYSTEM MOCK VIDEO", (20, 40), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            cv2.putText(frame, f"TIME: {curr_time}", (20, self.height - 30), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            cv2.putText(frame, "[VIRTUAL_CAMERA_SIM]", (20, self.height - 10), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 165, 255), 1)

            # 切片控制
            self._handle_video_segmentation(frame)
            self.data_hub.update_status({"is_recording": True})
            self._share_frame_to_hub(frame)
            
            elapsed = time.time() - start_time
            sleep_time = max(0.001, frame_interval - elapsed)
            time.sleep(sleep_time)

        self.data_hub.update_status({"is_recording": False})

    def _handle_video_segmentation(self, frame):
        """
        处理视频分段切片逻辑
        :param frame: 当前待写入的 OpenCV 视频帧
        """
        now = time.time()
        
        # 如果当前没有处于录制状态，或者已经超过了设定的切片时长，则新建一个视频文件
        if self.out is None or (now - self.segment_start_time >= self.segment_duration):
            self._close_writer()
            
            # 确保目录存在
            self.video_dir.mkdir(parents=True, exist_ok=True)
            
            # 创建新的视频文件名
            time_str = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.current_video_path = self.video_dir / f"y2h_video_{time_str}.mp4"
            
            # 使用 mp4v 编码器，具有很好的跨平台兼容性
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            self.out = cv2.VideoWriter(
                str(self.current_video_path),
                fourcc,
                self.fps,
                (self.width, self.height)
            )
            self.segment_start_time = now
            logging.info(f"开启新的视频录制切片: {self.current_video_path.name}")
            
        # 写入当前帧
        if self.out:
            self.out.write(frame)

    def _share_frame_to_hub(self, frame):
        """将当前帧作为属性暂存，供 UI 线程低延迟读取渲染"""
        # 为避免内存开销，直接在类属性中保留一个快速引用，不阻塞 Lock
        # 这种方式能够保证 UI 刷新时永远能抓到最新一帧，同时不需要将超大图像数据存进 data_hub 字典中
        self.last_frame = frame.copy()

    def get_latest_frame(self):
        """提供给本地 UI 组件调用的公开接口，用来获取最新画面预览"""
        if hasattr(self, 'last_frame'):
            return self.last_frame
        return None

    def _close_camera(self):
        """释放摄像头外设"""
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None

    def _close_writer(self):
        """安全关闭视频写入器，刷入元数据"""
        if self.out:
            try:
                self.out.release()
                logging.info(f"已成功保存并封包视频切片: {self.current_video_path.name}")
            except Exception as e:
                logging.error(f"关闭视频写入器失败: {e}")
        self.out = None
        self.current_video_path = None

    def _close_all(self):
        """释放所有句柄"""
        self._close_writer()
        self._close_camera()

# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 硬件驱动层：AIRMOD-X2 环境传感器驱动
负责读取并解析多合一环境传感器的串口数据，支持热插拔、自动重连与科学数据校准
"""
import time
import logging
import random
import serial
import config

class SensorWorker:
    def __init__(self, data_hub, stop_event):
        """
        初始化传感器采集工人类
        :param data_hub: 线程安全的数据中枢实例
        :param stop_event: 全局停止事件锁
        """
        self.data_hub = data_hub
        self.stop_event = stop_event
        self.port = config.SENSOR_PORT
        self.baud = config.SENSOR_BAUD
        self.ser = None
        self.is_mock = False

    def run(self):
        """线程执行入口"""
        logging.info("环境传感器线程 [Sensor-Worker] 已启动")
        
        while not self.stop_event.is_set():
            try:
                # 尝试打开串口
                if self.ser is None and not self.is_mock:
                    self.ser = serial.Serial(
                        port=self.port,
                        baudrate=self.baud,
                        timeout=2.0
                    )
                    logging.info(f"成功打开传感器串口: {self.port} (波特率: {self.baud})")
                    self.is_mock = False
                
                # 如果成功建立连接，进入读取循环
                if self.ser and self.ser.is_open:
                    self._read_and_parse_loop()
                elif self.is_mock:
                    self._mock_data_loop()
                    
            except serial.SerialException as e:
                logging.warning(f"传感器串口异常: {e}。10秒后尝试重连...")
                self._close_serial()
                self.data_hub.update_status({"sensor_status": "ERROR"})
                # 指数退避或简单休眠，避免 CPU 飙升
                self.stop_event.wait(10.0)
                
            except Exception as e:
                logging.error(f"环境传感器处理线程未知错误: {e}")
                self.stop_event.wait(5.0)

        # 线程退出前的清场
        self._close_serial()
        logging.info("环境传感器线程 [Sensor-Worker] 已优雅安全退出。")

    def _close_serial(self):
        """关闭串口连接"""
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def _read_and_parse_loop(self):
        """
        物理串口读取核心循环。
        针对 AIRMOD-X2 (通常为20字节长或类似的十六进制多合一传感器帧协议) 进行硬解析。
        """
        # AIRMOD-X2 常规协议包：
        # 起始双字节: 0x3C 0x02 或 0x42 0x4D 等 (此处以常见的 Luftmy / AIRMOD 十六进制协议为蓝本)
        # 帧长度: 20 字节
        frame_len = 20
        buffer = bytearray()
        
        while not self.stop_event.is_set():
            if self.ser.in_waiting > 0:
                # 逐个字节读取，用于精确帧同步，应对走航抖动
                data_byte = self.ser.read(1)
                if not data_byte:
                    continue
                    
                buffer.extend(data_byte)
                
                # 1. 寻找同步帧头 (以常见 0x3C 为帧头，0x02 为副帧头为例。可根据实际购买的 AIRMOD 硬件具体手册调整)
                if len(buffer) == 1 and buffer[0] != 0x3C:
                    buffer.clear()
                    continue
                if len(buffer) == 2 and buffer[1] != 0x02:
                    buffer.clear()
                    continue
                
                # 2. 如果满足包长度，进入校验和解析
                if len(buffer) >= frame_len:
                    data_packet = buffer[:frame_len]
                    buffer = buffer[frame_len:] # 截断 buffer
                    
                    parsed_dict = self._parse_airmod_packet(data_packet)
                    if parsed_dict:
                        # 3. 如果校验成功，更新到数据中枢
                        self.data_hub.update_sensor(parsed_dict)
            else:
                self.stop_event.wait(0.1) # 降低无数据时的 CPU 消耗

    def _parse_airmod_packet(self, packet):
        """
        解析 AIRMOD-X2 十六进制协议包
        :param packet: 完整的20字节 bytearray
        """
        try:
            # 协议和校验和验证 (最后一位通常为 Sum Of Bytes 0 to N-1)
            checksum = sum(packet[:-1]) & 0xFF
            if checksum != packet[-1]:
                logging.warning("传感器数据帧校验和不匹配，丢弃此帧")
                return None
            
            # 各物理参数解析 (通常大端字节序：High Byte * 256 + Low Byte)
            # 根据传感器出厂数据手册定义的偏置量(Offset)取值：
            pm25_raw = (packet[2] << 8) | packet[3]
            pm10_raw = (packet[4] << 8) | packet[5]
            
            # 科研级校准校正 (Y = A * X + B)
            pm25_cal = round(config.PM25_CAL_A * pm25_raw + config.PM25_CAL_B, 1)
            pm10_cal = round(pm10_raw * 1.0, 1) # PM10 保持原样或等效比例
            
            co2 = (packet[6] << 8) | packet[7]
            voc_raw = (packet[8] << 8) | packet[9]
            voc = round(voc_raw / 100.0, 2)  # 通常 VOC 原始值为 100 倍
            
            # 温度支持正负温度解析
            temp_raw = (packet[10] << 8) | packet[11]
            if temp_raw & 0x8000:  # 负温标志位（视具体协议而定）
                temp = -((temp_raw & 0x7FFF) / 10.0)
            else:
                temp = temp_raw / 10.0
                
            rh = ((packet[12] << 8) | packet[13]) / 10.0
            
            return {
                "pm25": pm25_cal,
                "pm10": pm10_cal,
                "temp": round(temp, 1),
                "rh": round(rh, 1),
                "voc": voc,
                "co2": co2,
                "sensor_status": "ACTIVE"
            }
        except Exception as e:
            logging.error(f"解析硬件传感器字节流出错: {e}")
            return None

    def _mock_data_loop(self):
        """
        传感器仿真模拟模式：
        方便在非树莓派设备上（如个人笔记本电脑）离线调试系统逻辑、UI 和云端存储
        """
        logging.info("物理串口不可用，正在自动切换至 [传感器仿真模式 (Mock Mode)] 运行...")
        
        # 预设基准参数，通过一阶马尔可夫链模拟真实环境波动的连续性
        sim_pm25 = 25.0
        sim_co2 = 450.0
        sim_temp = 22.0
        sim_rh = 60.0
        
        while not self.stop_event.is_set():
            # 产生连续的小幅度随机波动
            sim_pm25 = max(1.0, sim_pm25 + random.uniform(-1.5, 1.5))
            sim_co2 = max(400.0, sim_co2 + random.uniform(-10.0, 12.0))
            sim_temp = max(-10.0, min(40.0, sim_temp + random.uniform(-0.1, 0.1)))
            sim_rh = max(20.0, min(95.0, sim_rh + random.uniform(-0.5, 0.5)))
            
            mock_payload = {
                "pm25": round(sim_pm25, 1),
                "pm10": round(sim_pm25 * 1.3, 1),
                "temp": round(sim_temp, 1),
                "rh": round(sim_rh, 1),
                "voc": round(random.uniform(0.01, 0.35), 2),
                "co2": int(sim_co2),
                "sensor_status": "ACTIVE"
            }
            
            # 将生成的虚拟数据送进内存中枢
            self.data_hub.update_sensor(mock_payload)
            
            # 仿真 1 秒一次的采集周期
            self.stop_event.wait(config.LOG_INTERVAL)

    def trigger_mock_mode(self):
        """外部唤醒：跳过物理串口检测强制进入仿真模式"""
        self.is_mock = True
        self._close_serial()

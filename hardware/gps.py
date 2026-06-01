# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 硬件驱动层：GPS/GNSS 模块驱动
负责读取串口 NMEA 协议数据，进行坐标系转换、速度计算及定位状态机管理
"""
import time
import logging
import random
import serial
import config

class GPSWorker:
    def __init__(self, data_hub, stop_event):
        """
        初始化 GPS 采集工人类
        :param data_hub: 线程安全的数据中枢实例
        :param stop_event: 全局停止事件锁
        """
        self.data_hub = data_hub
        self.stop_event = stop_event
        self.port = config.GPS_PORT
        self.baud = config.GPS_BAUD
        self.ser = None
        self.is_mock = False
        
        # 本地暂存最新的 GPS 状态，因为 RMC 和 GGA 通常是分开的两条独立消息
        self._cache = {
            "lat": 0.0,
            "lon": 0.0,
            "speed_kmh": 0.0,
            "altitude": 0.0,
            "satellites": 0,
            "gps_status": "SEARCHING"
        }

    def run(self):
        """线程执行入口"""
        logging.info("定位追踪线程 [GPS-Worker] 已启动")
        
        while not self.stop_event.is_set():
            try:
                if self.ser is None and not self.is_mock:
                    self.ser = serial.Serial(
                        port=self.port,
                        baudrate=self.baud,
                        timeout=2.0
                    )
                    logging.info(f"成功打开 GPS 串口: {self.port}")
                    self.is_mock = False
                
                if self.ser and self.ser.is_open:
                    self._read_and_parse_loop()
                elif self.is_mock:
                    self._mock_data_loop()
                    
            except serial.SerialException as e:
                logging.warning(f"GPS 串口异常: {e}。10秒后尝试重连...")
                self._close_serial()
                self.data_hub.update_status({"gps_status": "ERROR"})
                self.stop_event.wait(10.0)
                
            except Exception as e:
                logging.error(f"GPS 处理线程未知错误: {e}")
                self.stop_event.wait(5.0)

        self._close_serial()
        logging.info("定位追踪线程 [GPS-Worker] 已优雅安全退出。")

    def _close_serial(self):
        """关闭串口连接"""
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def _read_and_parse_loop(self):
        """读取物理串口并按行解析 NMEA 数据"""
        while not self.stop_event.is_set():
            try:
                # NMEA 协议是 ASCII 文本格式，以 \r\n 结尾
                line = self.ser.readline().decode('ascii', errors='ignore').strip()
                if not line:
                    continue
                
                if line.startswith('$GNRMC') or line.startswith('$GPRMC'):
                    self._parse_rmc(line)
                elif line.startswith('$GNGGA') or line.startswith('$GPGGA'):
                    self._parse_gga(line)
                    
            except Exception as e:
                # 忽略偶尔的硬件字节乱码
                pass

    def _parse_rmc(self, line):
        """
        解析 RMC (Recommended Minimum Specific GNSS Data)
        包含: 时间, 状态(A=Active, V=Void), 纬度, 经度, 速度(节), 航向, 日期
        """
        parts = line.split(',')
        if len(parts) < 10:
            return

        status = parts[2]
        if status == 'V' or not parts[3] or not parts[5]:
            self._cache["gps_status"] = "SEARCHING"
            self.data_hub.update_gps({"gps_status": "SEARCHING"})
            return

        try:
            # 状态为 A (Active) 表示定位有效
            self._cache["gps_status"] = "LOCKED"
            
            # 解析纬度 DDMM.MMMM -> DD.DDDDDD
            lat_raw = parts[3]
            lat_dir = parts[4]
            self._cache["lat"] = self._nmea_to_decimal(lat_raw, lat_dir)
            
            # 解析经度 DDDMM.MMMM -> DD.DDDDDD
            lon_raw = parts[5]
            lon_dir = parts[6]
            self._cache["lon"] = self._nmea_to_decimal(lon_raw, lon_dir)
            
            # 解析速度 (1 节/Knot = 1.852 km/h)
            speed_knots = float(parts[7]) if parts[7] else 0.0
            self._cache["speed_kmh"] = round(speed_knots * 1.852, 2)
            
            # 将合并好的数据更新到数据中枢
            self.data_hub.update_gps(self._cache)
            
        except ValueError:
            pass

    def _parse_gga(self, line):
        """
        解析 GGA (Global Positioning System Fix Data)
        包含: 定位质量, 卫星数量, 海拔高度
        """
        parts = line.split(',')
        if len(parts) < 10:
            return
            
        try:
            # 解析搜星数量
            self._cache["satellites"] = int(parts[7]) if parts[7] else 0
            
            # 解析海拔高度 (米)
            self._cache["altitude"] = float(parts[9]) if parts[9] else 0.0
            
            # 将合并好的数据更新到数据中枢
            self.data_hub.update_gps(self._cache)
        except ValueError:
            pass

    def _nmea_to_decimal(self, nmea_str, direction):
        """
        将 NMEA 格式 (DDMM.MMMM) 转换为十进制度数 (DD.DDDDDD)
        """
        if not nmea_str:
            return 0.0
            
        # 找到小数点的索引
        dot_idx = nmea_str.find('.')
        if dot_idx == -1:
            return 0.0
            
        # 度数部分是小数点前两位之前的所有字符
        degrees = float(nmea_str[:dot_idx-2])
        # 分钟部分是剩下的字符
        minutes = float(nmea_str[dot_idx-2:])
        
        # 计算十进制数值
        decimal = degrees + (minutes / 60.0)
        
        # 南纬 (S) 和 西经 (W) 需要转为负数
        if direction in ['S', 'W']:
            decimal = -decimal
            
        return round(decimal, 6)

    def _mock_data_loop(self):
        """
        GPS 仿真模拟模式：
        模拟一辆车在地图上以 30-40 km/h 的速度沿东北方向行驶
        """
        logging.info("GPS 物理串口不可用，正在启动 [GPS轨迹仿真模式 (Mock Mode)]...")
        
        # 初始坐标设为一个典型的城市坐标 (如天安门附近)
        sim_lat = 39.9042
        sim_lon = 116.4074
        
        while not self.stop_event.is_set():
            # 模拟随机小幅移动 (约每秒移动10米)
            sim_lat += random.uniform(0.00005, 0.00015)
            sim_lon += random.uniform(0.00005, 0.00015)
            
            mock_payload = {
                "lat": round(sim_lat, 6),
                "lon": round(sim_lon, 6),
                "speed_kmh": round(random.uniform(20.0, 45.0), 1),
                "altitude": round(random.uniform(40.0, 50.0), 1),
                "satellites": random.randint(8, 14),
                "gps_status": "LOCKED"
            }
            
            self.data_hub.update_gps(mock_payload)
            
            # 模拟 1Hz 的 GPS 刷新率
            self.stop_event.wait(1.0)

    def trigger_mock_mode(self):
        """外部唤醒：跳过物理串口检测强制进入仿真模式"""
        self.is_mock = True
        self._close_serial()

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
        """
        self.data_hub = data_hub
        self.stop_event = stop_event
        self.port = config.GPS_PORT
        self.baud = config.GPS_BAUD
        self.ser = None
        self.is_mock = False
        
        # 本地暂存最新的 GPS 状态 (初始坐标设为南京市中心)
        self._cache = {
            "lat": 32.060255,
            "lon": 118.796877,
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
                elif line.startswith('$GPGSV') or line.startswith('$GNGSV'):
                    self._parse_gsv(line)
                    
            except Exception as e:
                # 忽略偶尔的硬件字节乱码
                pass

    def _parse_rmc(self, line):
        """解析 RMC"""
        parts = line.split(',')
        if len(parts) < 10:
            return

        status = parts[2]
        if status == 'V' or not parts[3] or not parts[5]:
            self._cache["gps_status"] = "SEARCHING"
            self._cache["gps_state"] = "locating"  # 主动汇报：定位中
            self.data_hub.update_gps({"gps_status": "SEARCHING"})
            return

        try:
            self._cache["gps_status"] = "LOCKED"
            self._cache["gps_state"] = "online"    # 主动汇报：硬件已锁定且在线
            
            lat_raw = parts[3]
            lat_dir = parts[4]
            self._cache["lat"] = self._nmea_to_decimal(lat_raw, lat_dir)
            
            lon_raw = parts[5]
            lon_dir = parts[6]
            self._cache["lon"] = self._nmea_to_decimal(lon_raw, lon_dir)
            
            speed_knots = float(parts[7]) if parts[7] else 0.0
            self._cache["speed_kmh"] = round(speed_knots * 1.852, 2)
            
            self.data_hub.update_gps(self._cache)
            
        except ValueError:
            pass

    def _parse_gga(self, line):
        """解析 GGA"""
        parts = line.split(',')
        if len(parts) < 10:
            return
            
        try:
            self._cache["satellites"] = int(parts[7]) if parts[7] else 0
            self._cache["hdop"] = float(parts[8]) if len(parts) > 8 and parts[8] else 99.9
            self._cache["altitude"] = float(parts[9]) if parts[9] else 0.0
            self.data_hub.update_gps(self._cache)
        except ValueError:
            pass

    def _parse_gsv(self, line):
        """解析 GSV 获取信噪比 SNR"""
        parts = line.split(',')
        # NMEA GSV 格式的信噪比 SNR 位于索引 7, 11, 15, 19
        snr_values = []
        for i in [7, 11, 15, 19]:
            if i < len(parts) and parts[i].strip() and parts[i] != '*':
                try:
                    # 去掉尾部可能附带的 *校验和 (例如 42*7E)
                    snr_str = parts[i].split('*')[0]
                    snr_values.append(float(snr_str))
                except ValueError:
                    pass
        
        if snr_values:
            # 取该报文中出现卫星的平均信噪比 (类比手机信号格数)
            avg_snr = round(sum(snr_values) / len(snr_values), 1)
            self._cache["snr"] = avg_snr
            # 单独推给 hub，因为 GSV 和 RMC 频率不同
            self.data_hub.update_gps({"snr": avg_snr})

    def _nmea_to_decimal(self, nmea_str, direction):
        if not nmea_str:
            return 0.0
        dot_idx = nmea_str.find('.')
        if dot_idx == -1:
            return 0.0
        degrees = float(nmea_str[:dot_idx-2])
        minutes = float(nmea_str[dot_idx-2:])
        decimal = degrees + (minutes / 60.0)
        if direction in ['S', 'W']:
            decimal = -decimal
        return round(decimal, 6)

    def _mock_data_loop(self):
        """仿真模式：在南京市中心附近随机移动"""
        logging.info("GPS 物理串口不可用，启动 [GPS轨迹仿真模式 (Mock Mode)]...")
        
        sim_lat = 32.060255
        sim_lon = 118.796877
        
        while not self.stop_event.is_set():
            sim_lat += random.uniform(-0.0001, 0.0001)
            sim_lon += random.uniform(-0.0001, 0.0001)
            
            mock_payload = {
                "lat": round(sim_lat, 6),
                "lon": round(sim_lon, 6),
                "speed_kmh": round(random.uniform(20.0, 45.0), 1),
                "altitude": round(random.uniform(10.0, 20.0), 1),
                "satellites": random.randint(8, 14),
                "gps_status": "LOCKED",
                "gps_state": "online",
                "hdop": round(random.uniform(0.8, 1.2), 1),
                "snr": round(random.uniform(38.0, 48.0), 1)
            }
            
            self.data_hub.update_gps(mock_payload)
            self.stop_event.wait(1.0)

    def trigger_mock_mode(self):
        self.is_mock = True
        self._close_serial()
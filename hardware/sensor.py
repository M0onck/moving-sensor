# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 硬件驱动层：AIRMOD-X2 环境传感器驱动
严格适配 18 字节 (0xAA) 私有十六进制协议
"""
import time
import logging
import serial
import config

class SensorWorker:
    def __init__(self, data_hub, stop_event):
        self.data_hub = data_hub
        self.stop_event = stop_event
        self.port = config.SENSOR_PORT
        self.baud = config.SENSOR_BAUD
        self.ser = None

    def run(self):
        logging.info("环境传感器线程 [Sensor-Worker] 已启动")
        buffer = bytearray()
        
        while not self.stop_event.is_set():
            try:
                if self.ser is None:
                    self.ser = serial.Serial(port=self.port, baudrate=self.baud, timeout=1.0)
                    logging.info(f"成功打开传感器串口: {self.port}")
                
                if self.ser.in_waiting > 0:
                    buffer.extend(self.ser.read(self.ser.in_waiting))
                
                # 协议解析：查找 0xAA 帧头
                while len(buffer) >= 18:
                    header_index = buffer.find(b'\xAA')
                    if header_index == -1:
                        buffer.clear()
                        break
                    if header_index > 0:
                        del buffer[:header_index]
                        continue
                    
                    # 尝试解析 18 字节数据包
                    packet = buffer[:18]
                    if self._validate_checksum(packet):
                        data = self._parse_packet(packet)
                        if data:
                            self.data_hub.update_sensor(data)
                        del buffer[:18]
                    else:
                        del buffer[0] # 校验失败，跳过当前字节
                        
                time.sleep(0.05)
            except Exception as e:
                logging.error(f"传感器采集异常: {e}")
                self._close_serial()
                time.sleep(5.0)

    def _validate_checksum(self, packet):
        # AIRMOD-X2 校验算法：前16字节累加和
        calc_sum = sum(packet[:16]) & 0xFFFF
        recv_sum = (packet[16] << 8) | packet[17]
        return calc_sum == recv_sum

    def _parse_packet(self, p):
        # 协议映射参考 AIRMOD-X2 数据手册
        try:
            return {
                "temp": ((p[2] << 8) | p[3]) / 10.0,
                "rh": ((p[4] << 8) | p[5]) / 10.0,
                "voc": (p[6] << 8) | p[7],
                "co2": (p[10] << 8) | p[11],
                "pm25": (p[12] << 8) | p[13],
                "pm10": (p[14] << 8) | p[15],
                "sensor_status": "ACTIVE"
            }
        except: return None

    def _close_serial(self):
        if self.ser:
            self.ser.close()
            self.ser = None

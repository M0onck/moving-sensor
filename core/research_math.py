# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 科研与数学算法层
负责时空单元划分、背景扣除（残差计算）、机会式骑手状态识别及坐标系转换
"""
import math
import hashlib
import bisect
import csv
from pathlib import Path
from datetime import datetime
from collections import deque
import logging

import config

# ==========================================
# 独立数学工具集：地理坐标系转换 (WGS84 -> GCJ02 -> BD09)
# ==========================================
X_PI = math.pi * 3000.0 / 180.0
PI = math.pi
A = 6378245.0
EE = 0.00669342162296594323

class GeoMath:
    @staticmethod
    def out_of_china(lon: float, lat: float) -> bool:
        return not (73.66 < lon < 135.05 and 3.86 < lat < 53.55)

    @staticmethod
    def transform_lat(lon: float, lat: float) -> float:
        ret = -100.0 + 2.0 * lon + 3.0 * lat + 0.2 * lat * lat + 0.1 * lon * lat + 0.2 * math.sqrt(abs(lon))
        ret += (20.0 * math.sin(6.0 * lon * PI) + 20.0 * math.sin(2.0 * lon * PI)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lat * PI) + 40.0 * math.sin(lat / 3.0 * PI)) * 2.0 / 3.0
        ret += (160.0 * math.sin(lat / 12.0 * PI) + 320 * math.sin(lat * PI / 30.0)) * 2.0 / 3.0
        return ret

    @staticmethod
    def transform_lon(lon: float, lat: float) -> float:
        ret = 300.0 + lon + 2.0 * lat + 0.1 * lon * lon + 0.1 * lon * lat + 0.1 * math.sqrt(abs(lon))
        ret += (20.0 * math.sin(6.0 * lon * PI) + 20.0 * math.sin(2.0 * lon * PI)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lon * PI) + 40.0 * math.sin(lon / 3.0 * PI)) * 2.0 / 3.0
        ret += (150.0 * math.sin(lon / 12.0 * PI) + 300.0 * math.sin(lon / 30.0 * PI)) * 2.0 / 3.0
        return ret

    @staticmethod
    def wgs84_to_gcj02(lon: float, lat: float) -> tuple:
        if GeoMath.out_of_china(lon, lat): return lon, lat
        dlat = GeoMath.transform_lat(lon - 105.0, lat - 35.0)
        dlon = GeoMath.transform_lon(lon - 105.0, lat - 35.0)
        radlat = lat / 180.0 * PI
        magic = math.sin(radlat)
        magic = 1 - EE * magic * magic
        sqrt_magic = math.sqrt(magic)
        dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrt_magic) * PI)
        dlon = (dlon * 180.0) / (A / sqrt_magic * math.cos(radlat) * PI)
        return lon + dlon, lat + dlat

    @staticmethod
    def wgs84_to_bd09(lon: float, lat: float) -> tuple:
        """国际标准 GPS (WGS84) 转 百度地图 (BD09)"""
        g_lon, g_lat = GeoMath.wgs84_to_gcj02(lon, lat)
        z = math.sqrt(g_lon * g_lon + g_lat * g_lat) + 0.00002 * math.sin(g_lat * X_PI)
        theta = math.atan2(g_lat, g_lon) + 0.000003 * math.cos(g_lon * X_PI)
        bd_lon = z * math.cos(theta) + 0.0065
        bd_lat = z * math.sin(theta) + 0.006
        return round(bd_lon, 6), round(bd_lat, 6)

# ==========================================
# 核心类：走航科研数据处理器
# ==========================================
class ResearchProcessor:
    def __init__(self):
        # 动态获取配置（兼容 config.py 中未完全定义的情况）
        self.is_research_mode = getattr(config, "MOBILE_RESEARCH_MODE", True)
        self.cell_size_m = getattr(config, "SPATIAL_CELL_SIZE_M", 50.0)
        self.time_bin_minutes = getattr(config, "TIME_BIN_MINUTES", 15)
        
        # 机会式骑手模式配置
        self.is_rider_mode = getattr(config, "OPPORTUNISTIC_RIDER_MODE", True)
        self.stop_speed = getattr(config, "STOP_SPEED_KMH", 1.5)
        self.slow_speed = getattr(config, "SLOW_SPEED_KMH", 8.0)
        self.fast_speed = getattr(config, "FAST_SPEED_KMH", 35.0)
        raw_rider_id = getattr(config, "RIDER_ID", "rider_unknown")
        self.rider_hash = self._anonymize_rider_id(raw_rider_id)
        self.run_id = getattr(config, "RUN_ID", datetime.now().strftime("run_%Y%m%d_%H%M%S"))
        
        # 缓存状态机
        self.mobile_buffer = deque(maxlen=getattr(config, "MOBILE_BASELINE_SAMPLES", 600))
        self.fixed_cache = {"path": getattr(config, "FIXED_STATION_CSV", ""), "mtime": None, "rows": [], "times": []}

    def _safe_float(self, val, default=None):
        try:
            return float(val) if val is not None and str(val).strip() != "" else default
        except ValueError:
            return default

    def _anonymize_rider_id(self, rider_id: str) -> str:
        """匿名化处理骑手ID（保护隐私）"""
        return hashlib.sha256(str(rider_id or "rider_unknown").encode("utf-8")).hexdigest()[:12]

    def _spatial_cell_id(self, lat: float, lon: float) -> str:
        """将经纬度映射到 50x50 米的空间网格 ID"""
        if lat is None or lon is None or self.cell_size_m <= 0:
            return ""
        lat_m = lat * 111320.0
        lon_m = lon * 111320.0 * max(0.01, math.cos(math.radians(lat)))
        return f"{int(math.floor(lon_m / self.cell_size_m))}_{int(math.floor(lat_m / self.cell_size_m))}"

    def _time_bin_label(self, t: datetime) -> str:
        """生成时间分箱标签（例如: 2026-05-15 08:15）"""
        mins = max(1, self.time_bin_minutes)
        floored_min = (t.minute // mins) * mins
        return t.replace(minute=floored_min, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")

    def _classify_movement(self, speed_kmh: float) -> tuple:
        """分类运动状态：停车 / 慢速 / 正常 / 快速"""
        if speed_kmh is None: return "unknown", "unknown"
        if speed_kmh < self.stop_speed: return "stopped", "stop"
        if speed_kmh < self.slow_speed: return "slow_moving", "slow"
        if speed_kmh < self.fast_speed: return "moving", "normal"
        return "fast_or_vehicle", "fast"

    def _calc_median(self, values: list) -> float:
        vals = sorted(v for v in values if v is not None)
        if not vals: return None
        mid = len(vals) // 2
        return vals[mid] if len(vals) % 2 != 0 else (vals[mid - 1] + vals[mid]) / 2

    def _mobile_only_background(self, pm25: float, pm10: float) -> dict:
        """滚动计算移动端伪基线（基于最近时间窗口的中位数）"""
        if pm25 is not None or pm10 is not None:
            self.mobile_buffer.append({"pm25": pm25, "pm10": pm10})
            
        if len(self.mobile_buffer) < 30:
            return {"mobile_baseline_source": f"warming_up_{len(self.mobile_buffer)}/{self.mobile_buffer.maxlen}"}
            
        med25 = self._calc_median([x.get("pm25") for x in self.mobile_buffer])
        med10 = self._calc_median([x.get("pm10") for x in self.mobile_buffer])
        return {
            "mobile_baseline_source": f"rolling_median_last_{len(self.mobile_buffer)}",
            "pm25_mobile_baseline": round(med25, 3) if med25 is not None else "",
            "pm10_mobile_baseline": round(med10, 3) if med10 is not None else ""
        }

    def enrich_snapshot(self, snapshot: dict) -> dict:
        """
        核心暴露方法：接收基础数据字典，计算并插入所有科研增强字段
        供 StorageWorker 在写入 CSV 之前调用。
        """
        # 为了不破坏原字典，我们深拷贝或者直接在原字典上更新，这里选择直接更新
        if not self.is_research_mode:
            return snapshot
            
        # 提取基础数据
        pm25 = self._safe_float(snapshot.get("pm25"))
        pm10 = self._safe_float(snapshot.get("pm10"))
        lat = self._safe_float(snapshot.get("lat"))
        lon = self._safe_float(snapshot.get("lon"))
        speed = self._safe_float(snapshot.get("speed_kmh"))
        now_dt = datetime.fromtimestamp(snapshot.get("timestamp", datetime.now().timestamp()))
        
        # 1. 空间与时间聚合标识
        cell_id = self._spatial_cell_id(lat, lon)
        time_bin = self._time_bin_label(now_dt)
        snapshot["spatial_cell_50m"] = cell_id
        snapshot["time_bin"] = time_bin
        snapshot["opportunistic_unit_id"] = f"{time_bin}|{cell_id}" if cell_id else time_bin
        
        # 2. 骑手运动学状态
        movement_state, speed_class = self._classify_movement(speed)
        snapshot["movement_state"] = movement_state
        snapshot["speed_class"] = speed_class
        snapshot["study_mode"] = "opportunistic_rider" if self.is_rider_mode else "planned_route"
        snapshot["run_id"] = self.run_id
        snapshot["rider_id_hash"] = self.rider_hash
        
        # 3. 移动端伪背景扣除（由于固定基站需要外部CSV，此处简化演示移动基线）
        mob_bg = self._mobile_only_background(pm25, pm10)
        snapshot.update(mob_bg)
        
        mob25 = self._safe_float(mob_bg.get("pm25_mobile_baseline"))
        if pm25 is not None and mob25 is not None and mob25 > 0:
            snapshot["pm25_mobile_residual"] = round(pm25 - mob25, 3)
            snapshot["pm25_mobile_ratio"] = round(pm25 / mob25, 4)
            
        # 4. BD09 百度坐标附加（方便后续云端渲染）
        if lat is not None and lon is not None:
            bd_lon, bd_lat = GeoMath.wgs84_to_bd09(lon, lat)
            snapshot["bd_lon"] = bd_lon
            snapshot["bd_lat"] = bd_lat
            
        return snapshot

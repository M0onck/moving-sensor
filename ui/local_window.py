# -*- coding: utf-8 -*-
"""
Y2H 移动环境感知系统 - 本地实时可视化界面 (Tkinter GUI)
负责在树莓派本地大屏上渲染干净的实时视频流、多维度指标卡片、GPS位置和公网扫描二维码
"""
import time
import logging
import base64
from datetime import datetime
import urllib.request
import urllib.parse
import json
import threading

import tkinter as tk
import cv2

import config
from core.research_math import GeoMath

class LocalUI:
    def __init__(self, data_hub, stop_event, camera_worker=None):
        """
        初始化 Tkinter 本地窗口界面
        :param data_hub: 线程安全的数据中枢实例
        :param stop_event: 全局停止事件锁
        :param camera_worker: 摄像头工作实例 (用于获取最后一帧图像进行无锁绘制)
        """
        self.data_hub = data_hub
        self.stop_event = stop_event
        self.camera_worker = camera_worker
        
        # 基础窗口参数配置
        self.fullscreen = getattr(config, "SCREEN_FULLSCREEN", False)
        self.geometry = getattr(config, "SCREEN_GEOMETRY", "1180x680")
        self.refresh_ms = getattr(config, "SCREEN_REFRESH_MS", 120)
        self.baidu_server_ak = getattr(config, "BAIDU_SERVER_AK", "1tW0leiAIZ79V7NrkLigvMAqpRUtwt2U")
        self.public_url = f"http://{config.CLOUD_SERVER_IP}:{config.CLOUD_SERVER_PORT}/"
        
        # 本地状态管理
        self.root = None
        self.card_labels = {}
        self.qr_cache_url = None
        
        # 逆地理编码（解析实际道路名称）相关变量
        self.last_lat = None
        self.last_lon = None
        self.last_lookup_time = 0.0
        self.current_address = "等待 GPS 定位..."

    def show(self):
        """运行 Tkinter 窗口主循环 (此方法会阻塞，直到窗口关闭)"""
        try:
            self.root = tk.Tk()
            self.root.title("Y2H 移动环境感知系统 - 本地大屏看板")
            self.root.configure(bg="#08111f")
            self.root.resizable(True, True)
            
            # 自适应屏幕分辨率
            if self.fullscreen:
                self.root.attributes("-fullscreen", True)
                base_w = max(900, self.root.winfo_screenwidth())
                base_h = max(560, self.root.winfo_screenheight())
            else:
                self.root.geometry(self.geometry)
                try:
                    geom = self.geometry.lower().split("+")[0]
                    base_w, base_h = [int(x) for x in geom.split("x", 1)]
                except Exception:
                    base_w, base_h = 1180, 680
                self.root.minsize(760, 430)

            # 自适应计算视频框和卡片框宽度
            self.camera_w = int(base_w * 0.55)
            self.camera_h = max(220, base_h - 92)
            self.value_wrap = max(180, base_w - self.camera_w - 100)

            self._build_layout()
            self._start_address_lookup_thread() # 启动后台解析道路线程
            self._refresh_loop()
            
            # 运行 Tkinter 消息循环
            self.root.mainloop()
            
        except Exception as e:
            logging.error(f"本地 UI 渲染启动失败: {e}")
            self.stop_event.set()

    def _build_layout(self):
        """构建 Tkinter 现代化深色栅格布局"""
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=5)
        self.root.grid_columnconfigure(1, weight=2)

        # ------------------ 头部 Title Bar ------------------
        header = tk.Frame(self.root, bg="#0f172a", height=56)
        header.grid(row=0, column=0, columnspan=2, sticky="nsew")
        header.grid_propagate(False)

        title_box = tk.Frame(header, bg="#0f172a")
        title_box.pack(side="left", padx=14, fill="y")
        
        tk.Label(
            title_box,
            text="Y2H 移动环境感知与走航监测系统",
            font=("Arial", 16, "bold"),
            bg="#0f172a",
            fg="#e5f2ff",
            anchor="w",
        ).pack(anchor="w", pady=(5, 0))
        
        tk.Label(
            title_box,
            text="树莓派 5 边缘端系统 · 实时环境数据 · GPS 轨迹追溯 · 车载视频采集",
            font=("Arial", 8),
            bg="#0f172a",
            fg="#93c5fd",
            anchor="w",
        ).pack(anchor="w", pady=(0, 4))

        # ------------------ 左侧 视频大视窗 ------------------
        camera_panel = tk.Frame(self.root, bg="#020617", bd=0, highlightbackground="#1e293b", highlightthickness=1)
        camera_panel.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=10)
        camera_panel.grid_rowconfigure(0, weight=1)
        camera_panel.grid_columnconfigure(0, weight=1)

        self.camera_label = tk.Label(
            camera_panel,
            text="等待摄像头画面捕获...",
            font=("Arial", 16, "bold"),
            bg="#020617",
            fg="#94a3b8",
            compound="center",
        )
        self.camera_label.grid(row=0, column=0, sticky="nsew")

        # ------------------ 右侧 数据状态卡片区 ------------------
        data_panel = tk.Frame(self.root, bg="#f8fafc", bd=0, highlightbackground="#cbd5e1", highlightthickness=1)
        data_panel.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=10)
        data_panel.grid_columnconfigure(0, weight=1)

        # 逆地理编码显示卡片 (路名信息)
        address_card = tk.Frame(data_panel, bg="#111827", bd=0)
        address_card.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 8))
        address_card.grid_columnconfigure(0, weight=1)
        
        tk.Label(
            address_card,
            text="📌 当前路段 (车载走航)",
            font=("Arial", 10, "bold"),
            bg="#111827",
            fg="#93c5fd",
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(7, 0))
        
        self.address_label = tk.Label(
            address_card,
            text="等待定位解析...",
            font=("Arial", 12, "bold"),
            bg="#111827",
            fg="#ffffff",
            anchor="w",
            justify="left",
            wraplength=self.value_wrap,
        )
        self.address_label.grid(row=1, column=0, sticky="ew", padx=10, pady=(2, 8))

        # 公网大屏推流展示二维码卡片
        qr_card = tk.Frame(data_panel, bg="#ffffff", bd=0, highlightbackground="#cbd5e1", highlightthickness=1)
        qr_card.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))
        qr_card.grid_columnconfigure(1, weight=1)
        
        self.qr_canvas = tk.Canvas(
            qr_card,
            width=130,
            height=130,
            bg="#ffffff",
            highlightthickness=1,
            highlightbackground="#cbd5e1",
        )
        self.qr_canvas.grid(row=0, column=0, rowspan=2, padx=8, pady=8)
        
        tk.Label(
            qr_card,
            text="云端大屏同步看板",
            font=("Arial", 10, "bold"),
            bg="#ffffff",
            fg="#2563eb",
            anchor="w",
        ).grid(row=0, column=1, sticky="ew", padx=(0, 8), pady=(8, 0))
        
        self.qr_url_label = tk.Label(
            qr_card,
            text=self.public_url,
            font=("Arial", 8),
            bg="#ffffff",
            fg="#475569",
            anchor="w",
            justify="left",
            wraplength=self.value_wrap,
        )
        self.qr_url_label.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(0, 8))

        # 动态多合一指标卡片网格
        self.cards_frame = tk.Frame(data_panel, bg="#f8fafc")
        self.cards_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=0)
        self.cards_frame.grid_columnconfigure(0, weight=1)
        self.cards_frame.grid_columnconfigure(1, weight=1)

        self._make_metric_card(0, 0, "温度 / 湿度", "temp_rh", "#dc2626")
        self._make_metric_card(0, 1, "VOC / CO₂", "voc_co2", "#7c3aed")
        self._make_metric_card(1, 0, "PM2.5 / PM10", "pm", "#ea580c")
        self._make_metric_card(1, 1, "定位坐标", "gps", "#0891b2")
        self._make_metric_card(2, 0, "走航速度 / 搜星数", "gps_detail", "#0d9488")
        self._make_metric_card(2, 1, "系统电量 / 电压", "battery", "#2563eb")

        # 底部系统运行计时
        self.footer = tk.Frame(data_panel, bg="#f8fafc")
        self.footer.grid(row=3, column=0, sticky="sew", padx=10, pady=(4, 8))
        self.footer.grid_columnconfigure(0, weight=1)
        
        self.time_label = tk.Label(
            self.footer,
            text="时间：-  运行：-",
            font=("Arial", 8),
            bg="#f8fafc",
            fg="#475569",
            anchor="w",
        )
        self.time_label.grid(row=0, column=0, sticky="ew")

        # 键盘退出事件映射 (Q 或 Esc 退出)
        self.root.bind("<Escape>", self._stop_gui)
        self.root.bind("q", self._stop_gui)
        self.root.bind("Q", self._stop_gui)
        self.root.protocol("WM_DELETE_WINDOW", self._stop_gui)

    def _make_metric_card(self, row, col, title, key, accent_color):
        """生成指标显示小卡片"""
        card = tk.Frame(self.cards_frame, bg="#ffffff", bd=0, highlightbackground="#e2e8f0", highlightthickness=1)
        card.grid(row=row, column=col, sticky="nsew", padx=4, pady=4)
        card.grid_columnconfigure(0, weight=1)
        
        tk.Label(
            card,
            text=title,
            font=("Arial", 9, "bold"),
            bg="#ffffff",
            fg=accent_color,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=8, pady=(5, 0))
        
        val_label = tk.Label(
            card,
            text="-",
            font=("Arial", 11, "bold"),
            bg="#ffffff",
            fg="#0f172a",
            anchor="w",
            justify="left",
            wraplength=max(78, self.value_wrap // 2 - 18),
        )
        val_label.grid(row=1, column=0, sticky="ew", padx=8, pady=(2, 6))
        self.card_labels[key] = val_label

    def _draw_qr_on_canvas(self, text):
        """在 Canvas 上原生渲染防错乱的二维码图像，避免 Tk 图像缓存丢失bug"""
        try:
            self.qr_canvas.delete("all")
            size = 130
            self.qr_canvas.create_rectangle(0, 0, size, size, fill="#ffffff", outline="#cbd5e1")
            
            import qrcode
            qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=1, border=2)
            qr.add_data(text)
            qr.make(fit=True)
            matrix = qr.get_matrix()
            n = len(matrix)
            if n <= 0:
                raise ValueError()
                
            quiet_border = 4
            usable_pixel_size = size - (2 * quiet_border)
            cell_pixel_size = max(1, usable_pixel_size // n)
            qr_actual_size = cell_pixel_size * n
            
            # 居中偏移量
            x_offset = (size - qr_actual_size) // 2
            y_offset = (size - qr_actual_size) // 2
            
            for y, row in enumerate(matrix):
                yy = y_offset + y * cell_pixel_size
                for x, bit in enumerate(row):
                    if bit:
                        xx = x_offset + x * cell_pixel_size
                        self.qr_canvas.create_rectangle(
                            xx, yy, xx + cell_pixel_size, yy + cell_pixel_size,
                            fill="#000000", outline="#000000"
                        )
        except Exception:
            # 异常时绘制等待文本
            self.qr_canvas.delete("all")
            self.qr_canvas.create_rectangle(0, 0, 130, 130, fill="#ffffff", outline="#cbd5e1")
            self.qr_canvas.create_text(65, 65, text="扫描公网 IP\n同步查看地图", font=("Arial", 9, "bold"), fill="#475569", justify="center")

    def _start_address_lookup_thread(self):
        """后台线程：负责高吞吐量的异步百度地图逆地理道路查找"""
        def worker():
            while not self.stop_event.is_set():
                try:
                    snapshot = self.data_hub.get_snapshot()
                    lat = snapshot.get("lat", 0.0)
                    lon = snapshot.get("lon", 0.0)
                    
                    if lat and lon and (lat != 0.0 or lon != 0.0):
                        # 判断位移量或时间退避，避免频繁请求百度 API 被限流封号
                        now = time.time()
                        if (now - self.last_lookup_time >= getattr(config, "ADDRESS_LOOKUP_INTERVAL_SECONDS", 15)) or \
                           (self.last_lat is None or abs(lat - self.last_lat) > 0.0002 or abs(lon - self.last_lon) > 0.0002):
                            
                            self.last_lat, self.last_lon = lat, lon
                            self.last_lookup_time = now
                            
                            # 执行 Web 请求
                            addr_res = self._reverse_geocode_baidu(lat, lon)
                            if addr_res:
                                self.current_address = addr_res
                    
                    self.stop_event.wait(2.0) # 每2秒自检测一次位置状态
                except Exception:
                    self.stop_event.wait(5.0)
                    
        t = threading.Thread(target=worker, name="UI-Address-Lookup", daemon=True)
        t.start()

    def _reverse_geocode_baidu(self, lat, lon):
        """百度 API 经纬度逆地理解析"""
        if not self.baidu_server_ak or self.baidu_server_ak == "你的百度地图AK密钥":
            return "未配置百度逆地理 AK"
        try:
            params = {
                "ak": self.baidu_server_ak,
                "output": "json",
                "coordtype": "wgs84ll",
                "location": f"{lat},{lon}",
                "extensions_road": "true",
                "extensions_poi": "0",
                "radius": "100"
            }
            url = "https://api.map.baidu.com/reverse_geocoding/v3/?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get("status") == 0 and "result" in data:
                    comp = data["result"].get("addressComponent", {})
                    city = comp.get("city", "")
                    dist = comp.get("district", "")
                    roads = data["result"].get("roads", [])
                    road_name = roads[0].get("name") if roads else comp.get("street", "未知道路")
                    return f"{city}{dist} {road_name}"
            return None
        except Exception:
            return None

    def _refresh_loop(self):
        """UI 主循环重绘事件机制"""
        if self.stop_event.is_set():
            if self.root:
                self.root.destroy()
            return

        try:
            # 1. 安全捞取数据中枢快照
            d = self.data_hub.get_snapshot()
            
            # 2. 动态更新二维码（如果公网链接发生变化）
            if self.public_url != self.qr_cache_url:
                self.qr_cache_url = self.public_url
                self._draw_qr_on_canvas(self.public_url)
                self.qr_url_label.config(text=self.public_url)

            # 3. 渲染状态文本卡片
            self.address_label.config(text=self.current_address)
            self.card_labels["temp_rh"].config(text=f"{d.get('temp', '-')} ℃\n{d.get('rh', '-')} %")
            self.card_labels["voc_co2"].config(text=f"{d.get('voc', '-')} μg/m³\n{d.get('co2', '-')} ppm")
            self.card_labels["pm"].config(text=f"PM2.5: {d.get('pm25', '-')} μg/m³\nPM10: {d.get('pm10', '-')} μg/m³")
            self.card_labels["gps"].config(text=f"纬度: {d.get('lat', '-')}\n经度: {d.get('lon', '-')}")
            self.card_labels["gps_detail"].config(text=f"速度: {d.get('speed_kmh', '-')} km/h\n卫星: {d.get('satellites', '-')} 颗")
            self.card_labels["battery"].config(text=f"电量: {d.get('battery_pct', '-') or '--'}%\n电压: {d.get('battery_voltage_v', '-') or '--'}V")
            
            self.time_label.config(text=f"系统时间：{d.get('time_str', '-')}  CPU 温度：{d.get('cpu_temp', '-')} ℃  空闲磁盘：{d.get('disk_free_gb', '-')} GB")

            # 4. 高速绘制摄像头画面预览帧
            if self.camera_worker:
                frame = self.camera_worker.get_latest_frame()
                if frame is not None:
                    # 自适应计算等比例缩放
                    h, w = frame.shape[:2]
                    scale = min(self.camera_w / w, self.camera_h / h)
                    new_w, new_h = max(1, int(w * scale)), max(1, int(h * scale))
                    resized_frame = cv2.resize(frame, (new_w, new_h))
                    
                    # 转换 BGR -> RGB
                    cv_rgb = cv2.cvtColor(resized_frame, cv2.COLOR_BGR2RGB)
                    
                    # 转换为 Tkinter 原生的 PhotoImage 数据
                    # 注意：这是树莓派上最不依赖 PIL 和 ImageTK 的轻量级快速内存转换法
                    success, encoded_img = cv2.imencode(".png", cv_rgb)
                    if success:
                        b64_data = base64.b64encode(encoded_img.tobytes()).decode("ascii")
                        photo = tk.PhotoImage(data=b64_data, format="png")
                        self.camera_label.config(image=photo, text="")
                        self.camera_label.image = photo # 维持引用防止被垃圾回收器吃掉
                else:
                    self.camera_label.config(text="摄像头未开启 或 视频正在加载中...")
            
        except Exception as e:
            logging.error(f"UI 重绘主线程异常: {e}")

        # 下一帧自迭代循环
        if not self.stop_event.is_set() and self.root:
            self.root.after(self.refresh_ms, self._refresh_loop)

    def _stop_gui(self, event=None):
        """优雅捕获退出按键或系统关闭大屏请求"""
        logging.warning("本地 UI 窗体拦截到安全关闭信号。")
        self.stop_event.set()
        if self.root:
            try:
                self.root.destroy()
            except Exception:
                pass
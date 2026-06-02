#!/bin/bash
# Y2H 自适应启动脚本: launch.sh

# 1. 稍微等待几秒，确保操作系统的桌面和网卡已经加载完毕
sleep 5

# 2. 核心探测：读取树莓派底层的显卡状态 (兼容 Pi 4/5)
if grep -q "^connected" /sys/class/drm/card*-*/status 2>/dev/null; then
    echo "[Y2H Launcher] 物理显示器已接入，准备拉起图形引擎..."
    export Y2H_LIVE_WINDOW=True

    # 注入图形界面的环境变量 (兼容 X11 和 树莓派5的 Wayland)
    export DISPLAY=:0
    export WAYLAND_DISPLAY=wayland-0
    export XDG_RUNTIME_DIR=/run/user/$(id -u)
else
    echo "[Y2H Launcher] 未检测到显示器，进入无头模式 (Headless)..."
    export Y2H_LIVE_WINDOW=False
fi

# 3. 进入目录并启动主程序
cd /home/y2h/project/moving-sensor
/usr/bin/python3 main.py

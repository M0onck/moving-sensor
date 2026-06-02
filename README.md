
---

# Y2H 走航监测系统 - 开机自启与守护进程设置向导

本文档记录了如何利用 Linux Systemd 为 Y2H 系统配置**自适应开机自启**（支持带屏图形渲染模式与无头盲采模式自动切换），并实现崩溃自动重启的工业级守护功能。

## 1. 核心前置条件

在配置系统服务前，请确保项目主目录下已存在智能探测脚本 `launch.sh`，并且已赋予其可执行权限：

```bash
# 赋予脚本可执行权限
chmod +x /home/y2h/project/moving-sensor/launch.sh

```

## 2. 注册 Systemd 系统服务

我们需要在系统的 systemd 目录下创建一个新的服务配置文件。

**操作步骤：**

1. 使用 sudo 权限创建并编辑服务文件：
```bash
sudo nano /etc/systemd/system/y2h-sensor.service

```


2. 将以下配置内容完整粘贴进去并保存：

```ini
[Unit]
Description=Y2H Adaptive Mobile Sensor System
After=network.target graphical.target

[Service]
Type=simple
# 指定运行用户，确保有权限调用底层桌面窗口与串口资源
User=y2h
Environment=HOME=/home/y2h

# 执行自适应探测与启动脚本
ExecStart=/bin/bash /home/y2h/project/moving-sensor/launch.sh

# 工业级防宕机：进程崩溃或意外退出后，10 秒后自动拉起重启
Restart=always
RestartSec=10

[Install]
WantedBy=graphical.target

```

## 3. 激活与启动服务

保存文件后，需要重载 systemd 守护进程，并激活该服务。

```bash
# 1. 重新加载系统服务配置文件 (每次修改 service 文件后必须执行)
sudo systemctl daemon-reload

# 2. 设置为开机自启动
sudo systemctl enable y2h-sensor.service

# 3. 立即启动服务 (无需重启树莓派即可生效)
sudo systemctl start y2h-sensor.service

```

## 4. 日常运维命令速查手册 (Cheat Sheet)

在日常调试和运维中，请使用以下命令管理 Y2H 后台进程：

| 功能需求 | 执行命令 | 说明 |
| --- | --- | --- |
| **查看实时日志** | `journalctl -u y2h-sensor.service -f` | 追踪系统运行打印，按 `Ctrl+C` 退出查看 |
| **查看运行状态** | `sudo systemctl status y2h-sensor.service` | 检查是否为绿色 active (running) 或是否在循环重启 |
| **手动停止服务** | `sudo systemctl stop y2h-sensor.service` | 临时停止后台采集，释放串口资源，以便手动调试代码 |
| **手动重启服务** | `sudo systemctl restart y2h-sensor.service` | 代码更新后，一键重启后台服务使新代码生效 |
| **取消开机自启** | `sudo systemctl disable y2h-sensor.service` | 永久关闭该服务的开机自启功能 |

---

**💡 运维提示：**
本服务依赖于 `graphical.target`（图形界面加载完成）。当设备拔掉 HDMI 进入无头模式 (Headless) 时，`launch.sh` 脚本会自动探测底层显卡接口，并拦截图形界面渲染需求，确保纯后台静默采集的稳定运行。

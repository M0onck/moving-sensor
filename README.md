# Y2H 走航监测系统 - 边缘端开发与运维手册

本文档旨在帮助团队成员快速掌握 Y2H 边缘端（树莓派 5）的日常开发调试工作流，以及生产环境下的系统级守护进程（Systemd）部署与排查方法。

---

## 👨‍💻 第一部分：日常开发与调试流程

在进行代码迭代、新增传感器协议或排查 Bug 时，建议遵循以下标准开发流程：

### 1. 停止后台驻留进程
在手动运行代码前，**必须先停止后台守护进程**，否则会因为串口（如 `/dev/ttyAMA0`、`/dev/ttyAMA4`）被占用而导致本地运行报错：
```bash
sudo systemctl stop y2h-sensor.service

```

### 2. 前台运行与调试

进入项目主目录，直接使用 Python 运行主程序。此时所有的日志、警告和报错信息都会直接输出在终端，方便实时排错：

```bash
cd /home/y2h/project/moving-sensor
python3 main.py

```

*提示：使用 `Ctrl + C` 可以安全终止程序的运行。*

### 3. 应用代码更新

代码修改测试通过后，为了让后台服务加载最新代码，请重新启动系统服务：

```bash
sudo systemctl restart y2h-sensor.service

```

---

## 🛡️ 第二部分：守护进程与开机自启设置

在正式的外场走航采集中，系统由 Linux Systemd 托管，支持**自适应带屏/无头盲采模式切换**以及**进程崩溃自动重启**。

### 1. 检查系统服务是否已启用

在接手一台新下发的设备或准备进行路测前，请首先检查服务状态：

```bash
systemctl status y2h-sensor.service

```

* **🟢 active (running)**: 服务正在后台健康运行。可以直接拔掉显示器出门采集。
* **🔴 inactive (dead)**: 服务被手动停止了，需要执行 `sudo systemctl start y2h-sensor.service` 唤醒。
* **⚪ Unit could not be found**: 该机器尚未配置守护进程，**请继续执行下方的全新部署流程**。

---

### 2. 全新部署流程（适用于新机器或重装系统）

若服务未配置或需要在新机器上重新设置，请严格遵循以下流程：

#### 2.1 核心前置条件

确保项目主目录下已存在智能探测脚本 `launch.sh`，并且已赋予其操作系统的可执行权限：

```bash
chmod +x /home/y2h/project/moving-sensor/launch.sh

```

#### 2.2 注册 Systemd 系统服务

使用 sudo 权限创建并编辑系统服务文件：

```bash
sudo nano /etc/systemd/system/y2h-sensor.service

```

将以下配置内容完整粘贴进去并保存（按 `Ctrl+O`，`Enter`，`Ctrl+X`）：

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

#### 2.3 激活与启动服务

保存文件后，重载 systemd 守护进程并彻底激活该服务：

```bash
# 1. 重新加载系统服务配置文件 (修改 service 文件后必须执行)
sudo systemctl daemon-reload

# 2. 设置为开机自启动
sudo systemctl enable y2h-sensor.service

# 3. 立即启动服务 (无需重启树莓派即可生效)
sudo systemctl start y2h-sensor.service

```

---

## 🛠️ 第三部分：日常运维命令速查 (Cheat Sheet)

在外场排障时，请使用以下命令管理后台进程：

| 功能需求 | 执行命令 | 说明 |
| --- | --- | --- |
| **查看实时日志** | `journalctl -u y2h-sensor.service -f` | 实时追踪后台运行输出与报错，按 `Ctrl+C` 退出查看。 |
| **查看运行状态** | `systemctl status y2h-sensor.service` | 检查是否处于绿色 `active` 或是否在无限循环重启。 |
| **手动停止服务** | `sudo systemctl stop y2h-sensor.service` | **开发前必做**。临时停止后台，释放硬件串口。 |
| **手动重启服务** | `sudo systemctl restart y2h-sensor.service` | **更新代码后必做**。一键重启使最新代码生效。 |
| **取消开机自启** | `sudo systemctl disable y2h-sensor.service` | 永久关闭该服务的开机自启功能。 |

> **💡 底层机制提示：**
> 本服务配置依赖于 `graphical.target`（图形界面准备完毕）。当外场设备拔掉 HDMI 进入无头模式 (Headless) 时，`launch.sh` 脚本会自动探测底层物理显卡接口，拦截 Tkinter 图形界面渲染需求，确保纯后台静默采集的高效稳定运行。

```

```

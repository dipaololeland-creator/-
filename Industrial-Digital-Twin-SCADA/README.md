<p align="center">
  <strong>🏭 Industrial Digital Twin & SCADA System</strong><br>
  <em>基于 IT/OT 融合架构的工业数字孪生双轨验证平台</em>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Protocol-Modbus%20TCP-green" alt="Modbus">
  <img src="https://img.shields.io/badge/Protocol-S7%20(Snap7)-orange" alt="S7">
  <img src="https://img.shields.io/badge/PLC-Siemens%20S7--1200-009999" alt="PLC">
  <img src="https://img.shields.io/badge/MQTT-Mosquitto-660099" alt="MQTT">
  <img src="https://img.shields.io/badge/HMI-Flask%20%2B%20ECharts-red" alt="HMI">
  <img src="https://img.shields.io/badge/License-MIT-brightgreen" alt="License">
</p>

---

## 📖 项目简介

本项目是一套 **基于 IT/OT 融合架构的工业数字孪生双轨验证系统**，完整覆盖从 PLC 层、边缘采集层、消息中间件层到上位机 HMI 层的全栈数据链路。

系统提供两条独立验证轨道，支持**纯软件仿真**与**软硬件在环（HIL）** 两种场景并行开发与调试：

| 轨道 | 场景 | 通信协议 | PLC 来源 |
|------|------|---------|---------|
| **轨道 A** — 纯软仿真 | 无需任何硬件，全栈纯 Python 模拟 | Modbus TCP | `virtual_plc.py` 软件模拟 |
| **轨道 B** — 软硬件在环 | 连接 TIA Portal 博途仿真器 + NetToPLCsim | S7 Protocol (Snap7) | 西门子 S7-1200 PLCSim |

```
 ┌─────────────────────── 轨道 A：纯软仿真 ───────────────────────┐
 │  virtual_plc.py ──Modbus──▶ edge_gateway.py ──MQTT──▶ data_recorder.py  │
 │                                                    ▼                     │
 │                              web_hmi.py ◀──── SQLite DB                  │
 └──────────────────────────────────────────────────────────────────────────┘

 ┌─────────────── 轨道 B：软硬件在环 (HIL) ───────────────────────┐
 │  S7-1200 PLCSim ──S7──▶ edge_gateway_snap7.py ──MQTT──▶ data_recorder.py│
 │  (TIA Portal)                                          ▼                 │
 │                           web_hmi_snap7.py ◀──── SQLite DB               │
 └──────────────────────────────────────────────────────────────────────────┘
```

---

## 📁 目录结构

```
Industrial-Digital-Twin-SCADA/
│
├── 📄 requirements.txt                # Python 第三方依赖清单
├── 📄 README.md                       # 本文件
│
├── 📂 01_Virtual_PLC_Simulation/      # ═══ 轨道 A：纯软仿真 ═══
│   ├── virtual_plc.py                 #   虚拟 PLC — Modbus TCP Server (端口 5020)
│   ├── edge_gateway.py                #   边缘网关 — Modbus 采集 → MQTT 发布
│   ├── DataCollector2.py              #   增强型数据采集器 — 封装类 + 后台线程
│   ├── data_recorder.py               #   数据记录器 — MQTT 订阅 → SQLite 持久化
│   ├── web_hmi.py                     #   Web HMI 监控台 — Flask + ECharts (端口 5000)
│   └── start_virtual_env.bat          #   🚀 Windows 一键启动脚本
│
└── 📂 02_TIA_Portal_HIL/             # ═══ 轨道 B：软硬件在环 ═══
    ├── edge_gateway_snap7.py          #   边缘网关 — Snap7 S7 采集 → MQTT 发布
    ├── data_recorder.py               #   数据记录器 — MQTT 订阅 → SQLite 持久化
    ├── web_hmi_snap7.py               #   Web HMI 监控台 — Flask + Snap7 (端口 5000)
    ├── start_hil_env.bat              #   🚀 Windows 一键启动脚本
    └── TIA_Project_Files/             #   TIA Portal 博途工程文件
```

---

## ✨ 核心亮点

### 🔄 有限状态机 (FSM) 驱动电机控制

PLC 侧 SCL 程序采用**有限状态机**架构管理电机状态转换（`IDLE → STARTING → RUNNING → STOPPING → IDLE`），取代传统的简单启停位直接翻转，确保每次状态变迁都有明确的前置条件与转换路径。

### 🛡️ 指令核销防呆机制

HMI/Web 端**禁止直接写入 `Motor_Status`**（DB1.DBX0.0），仅允许向指令区（DB1.DBX10.0 `Cmd_Start` / DB1.DBX10.1 `Cmd_Stop`）发送**脉冲信号**。PLC SCL 检测到指令位为 TRUE 后执行动作，并**自动将指令位复位为 FALSE**，实现"一写一清"核销，杜绝重复或误触发。

```
 HMI 按下"启动" → 写 Cmd_Start=TRUE → PLC 执行启动 → 自动核销 Cmd_Start=FALSE
                   （脉冲触发）          （状态机驱动）    （防呆复位）
```

### 🔒 双线圈互锁安全架构

PLC 程序中 `Cmd_Start` 与 `Cmd_Stop` 互斥处理 —— 同一扫描周期内若两个指令同时到达，只响应**停止指令**（安全优先），防止启停线圈同时带电导致的控制冲突。

### ⏱️ 看门狗超时监控

通信看门狗机制持续监测 HMI-PLC 之间的心跳。若在设定超时窗口内未收到合法指令，PLC 将自动触发安全保护动作（停机），防止因上位机掉线而导致设备失控。

### 📦 大块读取 + 本地解包

Snap7 版边缘网关采用 `db_read(1, 0, 10)` 一次性拉回 DB1 全部 10 字节原始 payload，在本地内存中按偏移量解析 Bool/Int/Real 各字段，**将网络 I/O 降至最低**。

---

## 🚀 快速启动

### 环境要求

| 项目 | 要求 |
|------|------|
| Python | 3.10+ |
| 操作系统 | Windows 10/11 |
| MQTT Broker | [Mosquitto](https://mosquitto.org/) 安装并运行 |
| 博途仿真器 | 仅轨道 B 需要：TIA Portal V16+ 与 PLCSim |
| NetToPLCsim | 仅轨道 B 需要：桥接仿真器到 TCP/IP 网络 |

### 1. 安装 Python 依赖

```bash
pip install -r requirements.txt
```

### 2. 启动 MQTT Broker

```bash
# 确保 Mosquitto 已运行在 127.0.0.1:1883
mosquitto -v
```

### 3A. 启动轨道 A — 纯软仿真（无需任何硬件）

双击运行一键启动脚本：

```
01_Virtual_PLC_Simulation\start_virtual_env.bat
```

脚本将按顺序启动 4 个服务窗口：

| 顺序 | 服务 | 说明 |
|------|------|------|
| 1 | `virtual_plc.py` | Modbus TCP Server，模拟 PLC 寄存器 |
| 2 | `edge_gateway.py` | Modbus 采集 → MQTT 发布 |
| 3 | `data_recorder.py` | MQTT 订阅 → SQLite 持久化 |
| 4 | `web_hmi.py` | Web HMI 监控台 |

启动完毕后，在浏览器访问 **http://127.0.0.1:5000/** 即可查看监控画面。

### 3B. 启动轨道 B — 软硬件在环 HIL

> ⚠️ 启动前请确认：TIA Portal PLCSim 已加载项目、NetToPLCsim 已运行、DB1 已组态。

双击运行一键启动脚本：

```
02_TIA_Portal_HIL\start_hil_env.bat
```

脚本将按顺序启动 3 个服务窗口：

| 顺序 | 服务 | 说明 |
|------|------|------|
| 1 | `edge_gateway_snap7.py` | S7 协议采集 → MQTT 发布 |
| 2 | `data_recorder.py` | MQTT 订阅 → SQLite 持久化 |
| 3 | `web_hmi_snap7.py` | Web HMI + Snap7 实时控制 |

启动完毕后，在浏览器访问 **http://127.0.0.1:5000/** 即可查看监控画面并远程控制 PLC 电机。

---

## 🛠️ 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| PLC 仿真层 | pymodbus / TIA Portal PLCSim | 虚拟 PLC / 博途仿真 PLC |
| 通信协议层 | Modbus TCP / S7 Protocol (Snap7) | 工业现场数据采集 |
| 消息中间件 | MQTT (paho-mqtt) + Mosquitto | 边缘网关 → 数据记录器 消息解耦 |
| 数据持久化 | SQLite | 轻量级本地历史数据存储 |
| 上位机 HMI | Flask + ECharts + Bootstrap | Web 端实时监控与远程控制 |

---

## 📜 License

本项目采用 [MIT License](LICENSE) 开源。

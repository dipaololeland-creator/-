@echo off
chcp 65001 >nul
title 工业数字孪生 - 软硬件在环 HIL 环境一键启动
color 0B

echo ╔══════════════════════════════════════════════════════════╗
echo ║   工业数字孪生 SCADA — 软硬件在环 HIL 环境 一键启动    ║
echo ║   Industrial Digital Twin — TIA Portal HIL Testing      ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

:: ── 设置工作目录为脚本所在文件夹 ────────────────────────────────
cd /d "%~dp0"
echo [信息] 工作目录: %cd%
echo.

:: ══════════════════════════════════════════════════════════════
::  前置检查提醒
:: ══════════════════════════════════════════════════════════════
echo ════════════════════════════════════════════════════════════
echo   ⚠  启动前请确认以下环境已就绪：
echo.
echo   1. TIA Portal 博途仿真器（PLCSim）已启动并加载项目
echo   2. NetToPLCsim 已运行，桥接 IP：192.168.74.128
echo   3. DB1 数据块已在 TIA Portal 中组态（关闭"优化块访问"）
echo   4. MQTT Broker（如 Mosquitto）已在 127.0.0.1:1883 运行
echo ════════════════════════════════════════════════════════════
echo.
echo 确认环境就绪后，按任意键开始启动服务...
pause >nul
echo.

:: ── 第 1 步：启动边缘网关 Snap7 版（S7 协议采集 → MQTT 发布）──
echo [1/3] 正在启动边缘网关 Snap7 版（S7 Protocol → MQTT）...
start "边缘网关(Snap7) - S7→MQTT" cmd /k "title 边缘网关(Snap7) - S7→MQTT && python edge_gateway_snap7.py"
echo       ✅ 边缘网关（Snap7 版）已启动
echo.

:: 等待 3 秒，确保 Snap7 连接建立
echo [等待] S7 通信链路建立中，请稍候 3 秒...
timeout /t 3 /nobreak >nul
echo.

:: ── 第 2 步：启动数据记录器（MQTT 订阅 → SQLite 持久化）──────
echo [2/3] 正在启动数据记录器（MQTT → SQLite）...
start "数据记录器 - MQTT→SQLite" cmd /k "title 数据记录器 - MQTT→SQLite && python data_recorder.py"
echo       ✅ 数据记录器已启动
echo.

:: 等待 2 秒，确保数据链路建立完毕
echo [等待] 数据链路建立中，请稍候 2 秒...
timeout /t 2 /nobreak >nul
echo.

:: ── 第 3 步：启动 Web HMI 监控台 Snap7 版（Flask，端口 5000）──
echo [3/3] 正在启动 Web HMI 监控台 Snap7 版（Flask, 端口 5000）...
start "Web HMI 监控台(Snap7)" cmd /k "title Web HMI 监控台(Snap7) && python web_hmi_snap7.py"
echo       ✅ Web HMI 监控台（Snap7 版）已启动
echo.

:: ── 启动完成 ─────────────────────────────────────────────────
echo ════════════════════════════════════════════════════════════
echo   ✅ 所有 HIL 服务已启动完毕！
echo.
echo   📊 数据链路：S7-1200 PLC → Snap7 边缘网关 → MQTT → 数据记录器 → SQLite
echo   🌐 监控台地址：http://127.0.0.1:5000/
echo.
echo   启动顺序：
echo     1. edge_gateway_snap7.py — S7 协议采集 → MQTT 发布
echo     2. data_recorder.py      — MQTT 订阅 → SQLite 持久化
echo     3. web_hmi_snap7.py      — Flask Web HMI + Snap7 控制 (端口 5000)
echo.
echo   控制架构：状态机 + 指令核销模式
echo     启动电机 → DB1.DBX10.0 (Cmd_Start) 脉冲
echo     停止电机 → DB1.DBX10.1 (Cmd_Stop) 脉冲
echo     PLC SCL 自动核销清零，防止指令重复执行
echo ════════════════════════════════════════════════════════════
echo.
echo 按任意键关闭此窗口（各服务窗口将保持运行）...
pause >nul

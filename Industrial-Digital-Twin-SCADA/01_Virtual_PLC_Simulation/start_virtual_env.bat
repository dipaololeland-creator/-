@echo off
chcp 65001 >nul
title 工业数字孪生 - 纯软仿真环境一键启动
color 0A

echo ╔══════════════════════════════════════════════════════════╗
echo ║     工业数字孪生 SCADA — 纯软仿真环境 一键启动脚本     ║
echo ║     Industrial Digital Twin — Virtual PLC Simulation    ║
echo ╚══════════════════════════════════════════════════════════╝
echo.

:: ── 设置工作目录为脚本所在文件夹 ────────────────────────────────
cd /d "%~dp0"
echo [信息] 工作目录: %cd%
echo.

:: ══════════════════════════════════════════════════════════════
::  前置提醒：请确保 MQTT Broker（如 Mosquitto）已在本机运行
:: ══════════════════════════════════════════════════════════════
echo ════════════════════════════════════════════════════════════
echo   ⚠  请确保 MQTT Broker（如 Mosquitto）已在 127.0.0.1:1883 运行
echo ════════════════════════════════════════════════════════════
echo.

:: ── 第 1 步：启动虚拟 PLC（Modbus TCP Server，端口 5020）──────
echo [1/4] 正在启动虚拟 PLC（Modbus TCP Server, 端口 5020）...
start "虚拟PLC - Modbus TCP Server" cmd /k "title 虚拟PLC - Modbus TCP Server && python virtual_plc.py"
echo       ✅ 虚拟 PLC 已启动
echo.

:: 等待 3 秒，确保 PLC 服务端准备就绪后再启动采集端
echo [等待] PLC 初始化中，请稍候 3 秒...
timeout /t 3 /nobreak >nul
echo.

:: ── 第 2 步：启动边缘网关（Modbus 采集 → MQTT 发布）──────────
echo [2/4] 正在启动边缘网关（Modbus → MQTT）...
start "边缘网关 - Modbus→MQTT" cmd /k "title 边缘网关 - Modbus→MQTT && python edge_gateway.py"
echo       ✅ 边缘网关已启动
echo.

:: ── 第 3 步：启动数据记录器（MQTT 订阅 → SQLite 持久化）──────
echo [3/4] 正在启动数据记录器（MQTT → SQLite）...
start "数据记录器 - MQTT→SQLite" cmd /k "title 数据记录器 - MQTT→SQLite && python data_recorder.py"
echo       ✅ 数据记录器已启动
echo.

:: 等待 2 秒，确保数据链路建立完毕
echo [等待] 数据链路建立中，请稍候 2 秒...
timeout /t 2 /nobreak >nul
echo.

:: ── 第 4 步：启动 Web HMI 监控台（Flask，端口 5000）──────────
echo [4/4] 正在启动 Web HMI 监控台（Flask, 端口 5000）...
start "Web HMI 监控台" cmd /k "title Web HMI 监控台 && python web_hmi.py"
echo       ✅ Web HMI 监控台已启动
echo.

:: ── 启动完成 ─────────────────────────────────────────────────
echo ════════════════════════════════════════════════════════════
echo   ✅ 所有服务已启动完毕！
echo.
echo   📊 数据链路：虚拟PLC → 边缘网关 → MQTT → 数据记录器 → SQLite
echo   🌐 监控台地址：http://127.0.0.1:5000/
echo.
echo   启动顺序：
echo     1. virtual_plc.py       — Modbus TCP Server (端口 5020)
echo     2. edge_gateway.py      — Modbus 采集 → MQTT 发布
echo     3. data_recorder.py     — MQTT 订阅 → SQLite 持久化
echo     4. web_hmi.py           — Flask Web HMI (端口 5000)
echo ════════════════════════════════════════════════════════════
echo.
echo 按任意键关闭此窗口（各服务窗口将保持运行）...
pause >nul

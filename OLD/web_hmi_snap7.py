# 依赖版本：python-snap7 1.4.1 / Flask 3.x
# 功能：Web HMI 监控台（Snap7 版本）。
#       通过 S7 协议直接读写西门子 S7-1200 的 DB 块，实现设备监控与远程控制。
#
# 与 web_hmi.py（Modbus 版）的核心区别：
#       Modbus 版 → 通过 Modbus TCP 功能码 06 写保持寄存器，适配虚拟 PLC
#       Snap7 版  → 通过 S7 协议直接写 DB 块字节，适配博途仿真 PLC（经 NetToPLCsim 桥接）
#
# DB1 数据块结构（需在 TIA Portal 中提前组态，与 edge_gateway_snap7.py 一致）：
#   ┌──────────┬────────────┬──────────┬──────────────────────────────────────────────┐
#   │ 偏移量    │ 变量名      │ 数据类型  │ 说明                                          │
#   ├──────────┼────────────┼──────────┼──────────────────────────────────────────────┤
#   │ 0.0      │ Motor_Status│ Bool     │ 电机运行状态：FALSE=停止 TRUE=运行（只读）      │
#   │ 2.0      │ Temperature │ Real     │ 环境温度，单位 ℃（4 字节浮点数）                │
#   │ 6.0      │ Motor_Speed │ Int      │ 电机转速，单位 RPM（2 字节整数）                │
#   │ 10.0     │ Cmd_Start   │ Bool     │ 启动指令脉冲：HMI 写 TRUE，PLC 自动核销清零     │
#   │ 10.1     │ Cmd_Stop    │ Bool     │ 停止指令脉冲：HMI 写 TRUE，PLC 自动核销清零     │
#   └──────────┴────────────┴──────────┴──────────────────────────────────────────────┘
#   总计：11 个字节
#
#   控制架构：状态机 + 指令核销模式
#     - HMI/Web 禁止直接写 Motor_Status（偏移 0.0），仅允许向 Cmd_Start/Cmd_Stop 发送脉冲
#     - PLC SCL 程序检测到指令位为 TRUE 后执行动作，并自动将指令位复位为 FALSE

import sqlite3
from flask import Flask, jsonify, request, render_template_string
import snap7                    # Snap7 S7 通信库
import snap7.util               # Snap7 数据解析工具（get_bool/set_bool/get_real/get_int）

# ── ANSI 终端颜色代码 ────────────────────────────────────────────────────────
GREEN  = "\033[32m"
RED    = "\033[31m"
YELLOW = "\033[33m"
RESET  = "\033[0m"

# 实例化 Flask 应用
app = Flask(__name__)

# ── Snap7 PLC 连接参数（与 edge_gateway_snap7.py 保持一致）─────────────────────
# 这组参数相当于 WinCC 里配置"S7 连接"时填的 PLC 站点信息
PLC_IP   = "192.168.74.128"   # PLC 的 IP 地址（通过 NetToPLCsim 桥接到博途仿真器）
PLC_RACK = 0               # 机架号：S7-1200/1500 固定为 0
PLC_SLOT = 1               # 槽号：S7-1200 固定为 1

# DB 块参数
DB_NUMBER    = 1    # 读写 DB1 数据块
DB_OFFSET    = 0    # 起始偏移量
DB_READ_SIZE = 8    # 状态区读取 8 个字节（覆盖 Motor_Status / Temperature / Motor_Speed）
CMD_OFFSET   = 10   # 指令区偏移量：Cmd_Start(10.0) / Cmd_Stop(10.1)

# ── 前端 HTML 模板 ────────────────────────────────────────────────────────────
# 在 Modbus 版基础上新增：实时 PLC 状态面板（直接从 DB 块读取当前值）
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>智能工厂设备监控台（Snap7 版）</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {
            background-color: #1e1e1e;
            color: #ffffff;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        h1 {
            color: #00adb5;
            text-align: center;
            margin-bottom: 10px;
        }
        .subtitle {
            color: #888;
            font-size: 14px;
            margin-bottom: 20px;
        }
        #chart-container {
            width: 800px;
            height: 400px;
            background-color: #2b2b2b;
            border: 1px solid #444;
            border-radius: 8px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.5);
        }
        #control-panel {
            margin: 20px 0;
            padding: 15px 30px;
            background-color: #2b2b2b;
            border: 1px solid #444;
            border-radius: 8px;
            display: flex;
            align-items: center;
            gap: 20px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.5);
        }
        #control-panel .panel-label {
            color: #00adb5;
            font-weight: bold;
            font-size: 16px;
        }
        #control-status {
            color: #aaa;
            font-size: 14px;
            margin-top: 8px;
            text-align: center;
        }
        /* PLC 实时状态面板 */
        #plc-status-panel {
            margin: 15px 0;
            padding: 15px 30px;
            background-color: #2b2b2b;
            border: 1px solid #444;
            border-radius: 8px;
            display: flex;
            align-items: center;
            gap: 30px;
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.5);
            font-size: 15px;
        }
        #plc-status-panel .status-item {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        #plc-status-panel .status-label {
            color: #888;
        }
        #plc-status-panel .status-value {
            font-weight: bold;
            color: #00bcd4;
        }
        /* 电机状态指示灯 */
        .motor-indicator {
            width: 14px;
            height: 14px;
            border-radius: 50%;
            display: inline-block;
            border: 1px solid #555;
        }
        .motor-on  { background-color: #4caf50; box-shadow: 0 0 8px #4caf50; }
        .motor-off { background-color: #666; }
    </style>
</head>
<body>

    <h1>智能工厂设备监控台</h1>
    <div class="subtitle">通信协议：S7 Protocol（Snap7）| 目标：PLC DB1 | 经 NetToPLCsim 桥接博途仿真器</div>

    <!-- PLC 实时状态面板：直接从 DB 块读取当前值 -->
    <div id="plc-status-panel">
        <div class="status-item">
            <span class="status-label">PLC 连接：</span>
            <span class="status-value" id="plc-conn-status">检测中...</span>
        </div>
        <div class="status-item">
            <span class="status-label">电机状态：</span>
            <span class="motor-indicator motor-off" id="motor-indicator"></span>
            <span class="status-value" id="motor-status-text">--</span>
        </div>
        <div class="status-item">
            <span class="status-label">实时温度：</span>
            <span class="status-value" id="live-temp">-- ℃</span>
        </div>
        <div class="status-item">
            <span class="status-label">实时转速：</span>
            <span class="status-value" id="live-speed">-- RPM</span>
        </div>
    </div>

    <!-- 电机控制面板 -->
    <div id="control-panel">
        <span class="panel-label">电机控制：</span>
        <button class="btn btn-success btn-lg" onclick="sendCommand('start')">
            ▶ 启动电机
        </button>
        <button class="btn btn-danger btn-lg" onclick="sendCommand('stop')">
            ■ 停止电机
        </button>
    </div>
    <div id="control-status">就绪 — 等待操作指令</div>

    <!-- ECharts 图表容器 -->
    <div id="chart-container"></div>

    <script>
        // ── ECharts 初始化 ──────────────────────────────────────────────────
        var chartDom = document.getElementById('chart-container');
        var myChart = echarts.init(chartDom, 'dark');

        var option = {
            backgroundColor: 'transparent',
            tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
            legend: {
                data: ['温度 (°C)', '转速 (RPM)'],
                textStyle: { color: '#ccc' }
            },
            xAxis: {
                type: 'category',
                data: [],
                axisLabel: { color: '#ccc' }
            },
            yAxis: [
                {
                    type: 'value', name: '温度', position: 'left',
                    axisLabel: { formatter: '{value} °C', color: '#ccc' },
                    splitLine: { lineStyle: { color: '#444' } }
                },
                {
                    type: 'value', name: '转速', position: 'right',
                    axisLabel: { formatter: '{value} RPM', color: '#ccc' },
                    splitLine: { show: false }
                }
            ],
            series: [
                {
                    name: '温度 (°C)', type: 'line', smooth: true,
                    itemStyle: { color: '#ff5722' }, data: []
                },
                {
                    name: '转速 (RPM)', type: 'bar', yAxisIndex: 1,
                    itemStyle: { color: '#00bcd4' }, data: []
                }
            ]
        };
        myChart.setOption(option);

        // ── 历史数据刷新（从 SQLite 数据库）────────────────────────────────
        function fetchAndUpdateData() {
            fetch('/api/history')
                .then(r => r.json())
                .then(data => {
                    myChart.setOption({
                        xAxis: { data: data.timestamps },
                        series: [
                            { data: data.temperatures },
                            { data: data.motor_speeds }
                        ]
                    });
                })
                .catch(err => console.error('获取历史数据失败:', err));
        }

        // ── PLC 实时状态刷新（直接从 DB 块读取）────────────────────────────
        function fetchPLCStatus() {
            fetch('/api/plc_status')
                .then(r => r.json())
                .then(data => {
                    var connEl    = document.getElementById('plc-conn-status');
                    var indEl     = document.getElementById('motor-indicator');
                    var motorEl   = document.getElementById('motor-status-text');
                    var tempEl    = document.getElementById('live-temp');
                    var speedEl   = document.getElementById('live-speed');

                    if (data.connected) {
                        connEl.textContent = '在线';
                        connEl.style.color = '#4caf50';

                        if (data.motor_status) {
                            indEl.className = 'motor-indicator motor-on';
                            motorEl.textContent = '运行';
                            motorEl.style.color = '#4caf50';
                        } else {
                            indEl.className = 'motor-indicator motor-off';
                            motorEl.textContent = '停止';
                            motorEl.style.color = '#f44336';
                        }
                        tempEl.textContent  = data.temperature.toFixed(1) + ' ℃';
                        speedEl.textContent = data.motor_speed + ' RPM';
                    } else {
                        connEl.textContent = '离线';
                        connEl.style.color = '#f44336';
                        indEl.className = 'motor-indicator motor-off';
                        motorEl.textContent = '--';
                        tempEl.textContent  = '-- ℃';
                        speedEl.textContent = '-- RPM';
                    }
                })
                .catch(err => {
                    console.error('获取PLC状态失败:', err);
                    document.getElementById('plc-conn-status').textContent = '通信异常';
                    document.getElementById('plc-conn-status').style.color = '#f44336';
                });
        }

        // 页面加载后立即刷新一次
        fetchAndUpdateData();
        fetchPLCStatus();

        // 定时刷新：历史数据 2 秒 / PLC 实时状态 1 秒
        setInterval(fetchAndUpdateData, 2000);
        setInterval(fetchPLCStatus, 1000);

        window.addEventListener('resize', function() { myChart.resize(); });

        // ── 电机控制函数 ────────────────────────────────────────────────────
        function sendCommand(command) {
            var statusDiv = document.getElementById('control-status');
            statusDiv.textContent = '⏳ 正在下发指令：' + (command === 'start' ? '启动电机' : '停止电机') + '...';
            statusDiv.style.color = '#ffab00';

            fetch('/api/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: command })
            })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    statusDiv.textContent = '✅ ' + data.message;
                    statusDiv.style.color = '#4caf50';
                } else {
                    statusDiv.textContent = '❌ ' + data.message;
                    statusDiv.style.color = '#f44336';
                }
            })
            .catch(err => {
                statusDiv.textContent = '❌ 通信异常：无法连接后端服务';
                statusDiv.style.color = '#f44336';
                console.error('控制指令发送失败:', err);
            });
        }
    </script>
</body>
</html>
"""


# ── Snap7 连接辅助函数 ────────────────────────────────────────────────────────

def snap7_connect():
    """
    创建 Snap7 客户端并连接到 S7 PLC。
    每次 API 请求时短连接，用完即断，避免 Flask 多线程下的连接冲突。

    返回：
        plc : snap7.client.Client（已连接），或 None（连接失败）
    """
    plc = snap7.client.Client()
    try:
        plc.connect(PLC_IP, PLC_RACK, PLC_SLOT)
        if plc.get_connected():
            return plc
    except Exception as e:
        print(f"{RED}[Web HMI-Snap7] Snap7 连接失败: {e}{RESET}")
    return None


# ── 路由：主页面 ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    """前端页面入口。"""
    return render_template_string(HTML_TEMPLATE)


# ── 路由：历史数据 API（从 SQLite 读取，与 Modbus 版完全一致）─────────────────

@app.route('/api/history', methods=['GET'])
def get_history_data():
    """
    查询 SQLite 数据库中最新 20 条历史记录，提供给前端 ECharts 绘图。
    数据由 data_recorder.py 写入，本接口只负责读取。
    """
    try:
        conn = sqlite3.connect('factory_data.db')
        cursor = conn.cursor()
        query = '''
            SELECT timestamp, temperature, motor_speed 
            FROM (
                SELECT timestamp, temperature, motor_speed 
                FROM history_data 
                ORDER BY timestamp DESC 
                LIMIT 20
            ) 
            ORDER BY timestamp ASC
        '''
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()

        timestamps   = [row[0] for row in rows]
        temperatures = [row[1] for row in rows]
        motor_speeds = [row[2] for row in rows]

        return jsonify({
            'timestamps':   timestamps,
            'temperatures': temperatures,
            'motor_speeds': motor_speeds
        })

    except Exception as e:
        print(f"数据读取异常: {e}")
        return jsonify({
            'timestamps': [], 'temperatures': [], 'motor_speeds': []
        })


# ── 路由：PLC 实时状态 API（Snap7 版新增）─────────────────────────────────────

@app.route('/api/plc_status', methods=['GET'])
def get_plc_status():
    """
    通过 Snap7 直接读取 PLC DB1 数据块，返回当前实时状态。

    读取过程：
        1. 建立 S7 连接 → db_read(1, 0, 8) 读取 8 字节
        2. 解析 Motor_Status (Bool@0.0)、Temperature (Real@2.0)、Motor_Speed (Int@6.0)
        3. 返回 JSON 给前端状态面板

    对比 Modbus 版：Modbus 版没有这个接口，只能看历史数据。
    Snap7 版可以实时看到 PLC 当前值，体验更接近真实 WinCC。
    """
    plc = snap7_connect()
    if plc is None:
        return jsonify({'connected': False})

    try:
        # 读取 DB1，偏移 0，共 8 字节
        db_data = plc.db_read(DB_NUMBER, DB_OFFSET, DB_READ_SIZE)

        # 解析各字段
        motor_status = snap7.util.get_bool(db_data, 0, 0)       # DB1.DBX0.0
        temperature  = round(snap7.util.get_real(db_data, 2), 1) # DB1.DBD2
        motor_speed  = snap7.util.get_int(db_data, 6)            # DB1.DBW6

        plc.disconnect()

        return jsonify({
            'connected':    True,
            'motor_status': motor_status,
            'temperature':  temperature,
            'motor_speed':  motor_speed,
            'raw_bytes':    db_data.hex().upper()
        })

    except Exception as e:
        print(f"{RED}[Web HMI-Snap7] 读取 DB 块失败: {e}{RESET}")
        try:
            plc.disconnect()
        except Exception:
            pass
        return jsonify({'connected': False})


# ── 路由：电机控制 API（Snap7 版）─────────────────────────────────────────────

@app.route('/api/control', methods=['POST'])
def control_motor():
    """
    电机控制接口（状态机 + 指令核销模式）。

    架构说明：
        PLC 底层已升级为状态机驱动，HMI/Web 端禁止直接篡改 Motor_Status（偏移 0.0）。
        上位机只能向指令区写入脉冲（Cmd_Start / Cmd_Stop），PLC SCL 检测到后执行动作
        并自动将指令位复位为 FALSE，实现"指令核销"。

    通信过程：
        1. 操作员点击"启动/停止"按钮         → 前端 POST /api/control
        2. 后端读出 DB1 第 10 字节（指令区）  → client.db_read(1, 10, 1)
        3. 置位对应指令位为 TRUE（脉冲）       → set_bool + client.db_write
        4. PLC 状态机检测指令位，执行启停动作   → PLC SCL 自动核销清零
        5. 后端返回写入结果给前端               → JSON 响应

    指令映射：
        启动电机 → DB1.DBX10.0 (Cmd_Start)  置 TRUE
        停止电机 → DB1.DBX10.1 (Cmd_Stop)   置 TRUE
    """
    try:
        # ── 步骤 1：解析前端指令 ─────────────────────────────────────────────
        data = request.get_json()
        command = data.get('command', '')

        if command not in ('start', 'stop'):
            return jsonify({'success': False, 'message': f'未知指令: {command}'}), 400

        # ── 步骤 2：建立 S7 连接 ─────────────────────────────────────────────
        plc = snap7_connect()
        if plc is None:
            return jsonify({
                'success': False,
                'message': 'S7 连接失败，请确认：\n1) 博途仿真器已启动\n2) NetToPLCsim 已运行\n3) PLC IP 配置正确'
            }), 503

        # ── 步骤 3：向 DB1 指令区写入脉冲 ────────────────────────────────────
        # 安全写位流程：先读出目标字节 → 修改指定位 → 写回整个字节
        # S7 的最小寻址单位是字节，不能直接写单个 bit

        # 读出 DB1 第 10 字节（指令区：Cmd_Start@10.0 / Cmd_Stop@10.1）
        cmd_byte = plc.db_read(DB_NUMBER, CMD_OFFSET, 1)

        if command == 'start':
            # 置位 Cmd_Start（DB1.DBX10.0）为 TRUE，向 PLC 发送启动脉冲
            # 注意：只写 True，不需要写 False，PLC SCL 会自动核销清零
            snap7.util.set_bool(cmd_byte, 0, 0, True)
            action_desc = "启动脉冲已发送 → DB1.DBX10.0 (Cmd_Start) = TRUE"

        else:  # command == 'stop'
            # 置位 Cmd_Stop（DB1.DBX10.1）为 TRUE，向 PLC 发送停止脉冲
            snap7.util.set_bool(cmd_byte, 0, 1, True)
            action_desc = "停止脉冲已发送 → DB1.DBX10.1 (Cmd_Stop) = TRUE"

        # 将修改后的字节写回 DB1 偏移 10（指令区）
        plc.db_write(DB_NUMBER, CMD_OFFSET, cmd_byte)

        # ── 步骤 4：断开 S7 连接 ─────────────────────────────────────────────
        plc.disconnect()

        # ── 步骤 5：反馈结果 ─────────────────────────────────────────────────
        print(f"{GREEN}[Web HMI-Snap7] ✅ 指令已下发 | {action_desc}{RESET}")
        return jsonify({
            'success': True,
            'message': action_desc
        })

    except Exception as e:
        print(f"{RED}[Web HMI-Snap7] ❌ 控制接口异常: {e}{RESET}")
        try:
            plc.disconnect()
        except Exception:
            pass
        return jsonify({
            'success': False,
            'message': f'服务器内部错误: {str(e)}'
        }), 500


# ── 主程序入口 ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 65)
    print("  智能工厂 Web HMI 监控台（Snap7 版）")
    print("  功能：实时数据监控 + 电机远程控制")
    print("  协议：S7 Protocol（Snap7）")
    print(f"  目标：PLC {PLC_IP} (rack={PLC_RACK}, slot={PLC_SLOT})")
    print(f"  DB 块：DB{DB_NUMBER} | 状态区 Motor_Status(0.0) Temperature(2.0) Motor_Speed(6.0)")
    print(f"  指令区：Cmd_Start(10.0) Cmd_Stop(10.1) — 状态机脉冲核销模式")
    print("=" * 65)
    print("⚠️  启动前请确认：")
    print("    1. 博途仿真器（PLCSim）已启动并加载项目")
    print("    2. NetToPLCsim 已运行，桥接 IP 为", PLC_IP)
    print('    3. DB1 数据块已在 TIA Portal 中组态（且关闭了"优化块访问"）')
    print()
    print("监控台已启动，请在浏览器中访问 http://127.0.0.1:5000/")
    app.run(port=5000, debug=True)

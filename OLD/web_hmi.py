import sqlite3
from flask import Flask, jsonify, request, render_template_string
from pymodbus.client import ModbusTcpClient          # 【新增】Modbus TCP 客户端，用于向 PLC 写寄存器

# 实例化 Flask 应用，作为我们的轻量级 Web 服务器
app = Flask(__name__)

# ── Modbus 连接参数（与 virtual_plc.py 保持一致）────────────────────────────
# 这组参数相当于 WinCC 里配置"PLC 连接"时填的 IP 和端口
MODBUS_HOST = "127.0.0.1"   # 虚拟 PLC 地址（本机）
MODBUS_PORT = 5020           # 虚拟 PLC 监听端口

# 定义前端 HTML 与 JS 代码模板
# 采用深色工业风，并引入 ECharts 库用于数据可视化
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>智能工厂设备监控台</title>
    <!-- 引入 ECharts 库 (CDN) -->
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <!-- 【新增】引入 Bootstrap 5 样式 (CDN)，用于美化控制按钮 -->
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        /* 深色工业风背景设置 */
        body {
            background-color: #1e1e1e; /* 暗色调背景 */
            color: #ffffff; /* 白色文字 */
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        h1 {
            color: #00adb5; /* 标题使用青色，增加科技感 */
            text-align: center;
            margin-bottom: 30px;
        }
        /* 图表容器：居中显示，宽 800px，高 400px */
        #chart-container {
            width: 800px;
            height: 400px;
            background-color: #2b2b2b; /* 容器稍微亮一点的暗色 */
            border: 1px solid #444;
            border-radius: 8px; /* 圆角边框 */
            box-shadow: 0 4px 8px rgba(0, 0, 0, 0.5); /* 阴影效果 */
        }
        /* 【新增】控制面板样式 */
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
        /* 状态指示文字 */
        #control-status {
            color: #aaa;
            font-size: 14px;
            margin-top: 8px;
            text-align: center;
        }
    </style>
</head>
<body>

    <!-- 页面标题 -->
    <h1>智能工厂设备监控台</h1>

    <!-- 【新增】电机控制面板 ─────────────────────────────────────────────── -->
    <!-- 相当于 WinCC 画面上的"启动/停止"操作按钮 -->
    <div id="control-panel">
        <span class="panel-label">电机控制：</span>
        <!-- 启动按钮：绿色，对应"合闸启动" -->
        <button class="btn btn-success btn-lg" onclick="sendCommand('start')">
            ▶ 启动电机
        </button>
        <!-- 停止按钮：红色，对应"分闸停机" -->
        <button class="btn btn-danger btn-lg" onclick="sendCommand('stop')">
            ■ 停止电机
        </button>
    </div>
    <!-- 操作状态反馈区 -->
    <div id="control-status">就绪 — 等待操作指令</div>
    
    <!-- 放置 ECharts 图表的容器 -->
    <div id="chart-container"></div>

    <script>
        // 初始化 ECharts 实例
        var chartDom = document.getElementById('chart-container');
        var myChart = echarts.init(chartDom, 'dark'); // 启用 ECharts 内置的暗色主题
        var option;

        // 图表的基础配置方案
        option = {
            backgroundColor: 'transparent', // 透明背景，使用容器的背景色
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'cross' }
            },
            legend: {
                data: ['温度 (°C)', '转速 (RPM)'],
                textStyle: { color: '#ccc' }
            },
            xAxis: {
                type: 'category',
                data: [], // 时间戳数据后续通过 API 动态获取
                axisLabel: { color: '#ccc' }
            },
            yAxis: [
                {
                    type: 'value',
                    name: '温度',
                    position: 'left',
                    axisLabel: { formatter: '{value} °C', color: '#ccc' },
                    splitLine: { lineStyle: { color: '#444' } }
                },
                {
                    type: 'value',
                    name: '转速',
                    position: 'right',
                    axisLabel: { Formatter: '{value} RPM', color: '#ccc' },
                    splitLine: { show: false } // 隐藏右侧坐标轴的网格线，避免杂乱
                }
            ],
            series: [
                {
                    name: '温度 (°C)',
                    type: 'line', // 温度使用折线图显示趋势
                    smooth: true, // 平滑曲线
                    itemStyle: { color: '#ff5722' },
                    data: [] // 温度数据
                },
                {
                    name: '转速 (RPM)',
                    type: 'bar', // 转速使用柱状图显示具体数值
                    yAxisIndex: 1, // 对应右侧的 Y 轴
                    itemStyle: { color: '#00bcd4' },
                    data: [] // 转速数据
                }
            ]
        };

        // 将配置项应用到图表实例上
        option && myChart.setOption(option);

        // 数据刷新函数：向后端请求最新数据并更新图表
        function fetchAndUpdateData() {
            // 前端发起 HTTP GET 请求到后端接口
            // 数据流：前端 JS -> HTTP GET -> Flask 后端接口 -> SQLite 数据库
            fetch('/api/history')
                .then(response => response.json()) // 将后端的 JSON 响应解析为 JS 对象
                .then(data => {
                    // 数据流：解析后的 JSON 数据 -> ECharts 更新方法 -> 页面重新渲染
                    // 把获取到的数据分别填入图表配置的对应位置
                    myChart.setOption({
                        xAxis: {
                            data: data.timestamps // 更新 X 轴时间
                        },
                        series: [
                            { data: data.temperatures },   // 更新折线图数据（温度）
                            { data: data.motor_speeds }    // 更新柱状图数据（转速）
                        ]
                    });
                })
                .catch(error => console.error('获取监控数据失败:', error));
        }

        // 页面加载完成后立即获取一次数据
        fetchAndUpdateData();

        // 设定定时器，每隔 2000 毫秒（2秒）执行一次数据刷新函数
        setInterval(fetchAndUpdateData, 2000);

        // 监听窗口大小变化，让图表自适应缩放
        window.addEventListener('resize', function() {
            myChart.resize();
        });

        // ── 【新增】电机控制函数 ────────────────────────────────────────────
        // 相当于 WinCC 画面上按下"启动/停止"按钮后，上位机向 PLC 写寄存器的过程
        function sendCommand(command) {
            var statusDiv = document.getElementById('control-status');
            // 显示"指令下发中"的状态，相当于上位机正在与 PLC 握手通信
            statusDiv.textContent = '⏳ 正在下发指令：' + (command === 'start' ? '启动电机' : '停止电机') + '...';
            statusDiv.style.color = '#ffab00';

            // 向后端 Flask 接口发送 POST 请求，携带控制指令的 JSON 数据
            // 数据流：前端按钮点击 -> HTTP POST -> Flask 后端 -> Modbus TCP -> 虚拟 PLC 寄存器
            fetch('/api/control', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: command })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    statusDiv.textContent = '✅ ' + data.message;
                    statusDiv.style.color = '#4caf50';
                    alert('指令已下发：' + data.message);
                } else {
                    statusDiv.textContent = '❌ ' + data.message;
                    statusDiv.style.color = '#f44336';
                    alert('操作失败：' + data.message);
                }
            })
            .catch(error => {
                statusDiv.textContent = '❌ 通信异常：无法连接后端服务';
                statusDiv.style.color = '#f44336';
                console.error('控制指令发送失败:', error);
            });
        }
    </script>
</body>
</html>
"""

# 编写主页面路由 `/`
@app.route('/')
def index():
    """
    前端页面入口。
    当用户在浏览器访问根目录时，直接将上面的 HTML_TEMPLATE 字符串渲染为网页返回。
    """
    return render_template_string(HTML_TEMPLATE)

# 编写数据 API 路由 `/api/history`
@app.route('/api/history', methods=['GET'])
def get_history_data():
    """
    后端数据接口：负责查询数据库并将数据提供给前端。
    支持 GET 请求。前端通过 fetch 定时调用此接口。
    数据流：客户端请求 -> 连接数据库 -> 读取最新 20 条历史记录 -> 处理成列表 -> 转为 JSON 返回
    """
    try:
        # a) 连接本地的 SQLite 数据库文件 (factory_data.db)
        # 每次请求建立新的连接以保证线程安全并获取最新数据
        conn = sqlite3.connect('factory_data.db')
        cursor = conn.cursor()

        # b) 查询 `history_data` 表中最新的 20 条记录。
        # 这里使用子查询：先按 timestamp 降序排列抓取最新的20条，然后再按升序(ASC)翻转顺序。
        # 这样能保证取到最新的数据段，并在 ECharts 上从左到右按时间先后正确画图。
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
        
        # 不要忘记关闭数据库连接，释放资源
        conn.close()

        # c) 将查询到的数据按列提取，拆分成三个列表
        timestamps = []
        temperatures = []
        motor_speeds = []
        
        for row in rows:
            # row 的结构形如 (timestamp_value, temperature_value, motor_speed_value)
            timestamps.append(row[0])  
            temperatures.append(row[1]) 
            motor_speeds.append(row[2])       

        # 将这三个列表打包成 JSON 格式返回给前端引擎
        # Flask 的 jsonify 函数会自动将 Python 字典转为 JSON 格式并设置正确的网络响应头 (Content-Type: application/json)
        return jsonify({
            'timestamps': timestamps,
            'temperatures': temperatures,
            'motor_speeds': motor_speeds
        })

    except Exception as e:
        # 如果数据库尚未创建表或发生查询错误，打印错误日志
        print(f"数据读取异常: {e}")
        # 返回空的 JSON 结构，防止前端解析报错而导致页面崩溃
        return jsonify({
            'timestamps': [],
            'temperatures': [],
            'motor_speeds': []
        })

# ── 【新增】电机控制 API 路由 ─────────────────────────────────────────────────
# 这个接口实现了"上位机 → PLC"的反向控制
# 等价于 WinCC 画面上按下"启动/停止"按钮后，上位机通过通信协议向 PLC 写寄存器
@app.route('/api/control', methods=['POST'])
def control_motor():
    """
    电机控制接口：接收前端指令，通过 Modbus TCP 写入 PLC 寄存器。

    通信过程（对标真实工控场景）：
        1. 操作员在 HMI 画面点击"启动"按钮         → 前端发送 POST 请求
        2. 上位机程序组装写寄存器报文               → Flask 解析指令并调用 pymodbus
        3. 通过 Modbus TCP 功能码 06 写单个寄存器   → write_register() 执行
        4. PLC 收到写入请求，更新保持寄存器的值     → 虚拟 PLC 的值被改变
        5. 上位机确认写入成功，反馈给操作员         → 返回 JSON 响应给前端

    参数（POST JSON）：
        command: "start" — 启动电机（向转速寄存器写入 1500 RPM）
        command: "stop"  — 停止电机（向转速寄存器写入 0）
    """
    try:
        # ── 步骤 1：解析前端发来的 JSON 指令 ─────────────────────────────────
        data = request.get_json()
        command = data.get('command', '')

        if command not in ('start', 'stop'):
            return jsonify({'success': False, 'message': f'未知指令: {command}'}), 400

        # ── 步骤 2：建立与虚拟 PLC 的 Modbus TCP 连接 ───────────────────────
        # 相当于上位机通过网线/交换机连到 PLC 的通信端口
        modbus_client = ModbusTcpClient(host=MODBUS_HOST, port=MODBUS_PORT, timeout=5)

        if not modbus_client.connect():
            # 连接失败 = 通信链路断开（网线没插、PLC 没上电等）
            return jsonify({
                'success': False,
                'message': 'Modbus TCP 连接失败，请确认虚拟 PLC 已启动'
            }), 503

        # ── 步骤 3：根据指令，使用功能码 06（写单个保持寄存器）向 PLC 写值 ──
        # Modbus 功能码 06 = Write Single Register
        # 在真实项目中，这一步等价于上位机向 PLC 的 DB 块写入控制字
        if command == 'start':
            # 启动电机：
            #   1) 向地址 2（电机状态寄存器 40003）写入 1（运行）
            #   2) 向地址 3（转速寄存器 40004）写入 1500 RPM
            # 相当于操作员在触摸屏上按下"启动"并设定转速为 1500
            result_status = modbus_client.write_register(
                address=2,       # 偏移 2 → 电机状态寄存器 40003
                value=1,         # 1 = 运行
                device_id=1      # 从站地址（PLC 站号）
            )
            speed_value = 1500
            result_speed = modbus_client.write_register(
                address=3,       # 偏移 3 → 转速寄存器 40004
                value=speed_value,
                device_id=1
            )
            result = result_status if result_status.isError() else result_speed
            action_desc = f"启动电机，转速设定 {speed_value} RPM"

        else:  # command == 'stop'
            # 停止电机：
            #   1) 向地址 2（电机状态寄存器 40003）写入 0（停止）
            #   2) 向地址 3（转速寄存器 40004）写入 0
            # 相当于操作员按下"急停"或"停止"按钮
            result_status = modbus_client.write_register(
                address=2,       # 偏移 2 → 电机状态寄存器 40003
                value=0,         # 0 = 停止
                device_id=1
            )
            result_speed = modbus_client.write_register(
                address=3,       # 偏移 3 → 转速寄存器 40004
                value=0,
                device_id=1
            )
            result = result_status if result_status.isError() else result_speed
            action_desc = "停止电机，转速清零"

        # ── 步骤 4：关闭 Modbus 连接 ────────────────────────────────────────
        # 写完就断开，不长期占用通信端口（与 edge_gateway 的持久连接不同）
        modbus_client.close()

        # ── 步骤 5：检查写入结果并反馈给前端 ─────────────────────────────────
        if result.isError():
            # 写入失败：PLC 返回了异常响应（如地址越界、从站无响应等）
            print(f"\033[31m[Web HMI] ❌ Modbus 写入失败: {result}\033[0m")
            return jsonify({
                'success': False,
                'message': f'PLC 写入失败: {result}'
            }), 500

        # 写入成功：在终端打印绿色日志，方便调试
        print(f"\033[32m[Web HMI] ✅ 指令已下发 | {action_desc}\033[0m")
        return jsonify({
            'success': True,
            'message': action_desc
        })

    except Exception as e:
        print(f"\033[31m[Web HMI] ❌ 控制接口异常: {e}\033[0m")
        return jsonify({
            'success': False,
            'message': f'服务器内部错误: {str(e)}'
        }), 500

if __name__ == '__main__':
    # 启动 Flask 服务，监听 5000 端口
    # debug=True: 开启调试模式，代码修改后服务会自动重新加载，非常适合开发调试阶段
    print("=" * 60)
    print("  智能工厂 Web HMI 监控台")
    print("  功能：实时数据监控 + 电机远程控制")
    print("=" * 60)
    print("监控控制台已启动，请在浏览器中访问 http://127.0.0.1:5000/")
    app.run(port=5000, debug=True)

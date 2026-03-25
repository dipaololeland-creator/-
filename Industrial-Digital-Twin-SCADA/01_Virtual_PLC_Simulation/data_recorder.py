# 依赖版本：paho-mqtt 2.1.0
# 功能：MQTT 数据持久化脚本。
#       订阅边缘网关发布的传感器数据，解析后存入本地 SQLite 数据库，
#       作为数据链路的最后一环：虚拟 PLC → 边缘网关 → 数据记录器（本脚本）。

# ── 1. 导入所需库 ────────────────────────────────────────────────────────────
import paho.mqtt.client as mqtt                    # MQTT 客户端
from paho.mqtt.client import CallbackAPIVersion    # 回调 API 版本枚举（paho-mqtt 2.x）
import sqlite3                                     # SQLite 数据库（Python 标准库）
import json                                        # JSON 反序列化
from datetime import datetime                      # 获取当前时间戳

# ── 常量定义 ──────────────────────────────────────────────────────────────────
MQTT_BROKER = "127.0.0.1"          # MQTT Broker 地址（本机）
MQTT_PORT   = 1883                 # MQTT 默认端口
MQTT_TOPIC  = "factory/plc/data"   # 订阅主题，与边缘网关发布主题一致
DB_FILE     = "factory_data.db"    # SQLite 数据库文件名

# ANSI 终端颜色代码，用于在控制台输出彩色日志
GREEN = "\033[32m"
RED   = "\033[31m"
RESET = "\033[0m"

# ── 2. 数据库初始化 ──────────────────────────────────────────────────────────
def init_db():
    """
    连接（或创建）本地 SQLite 数据库，并建立历史数据表。

    表结构 - history_data：
        id           : 自增主键，每条记录唯一标识
        timestamp    : 数据写入时间（文本格式：YYYY-MM-DD HH:MM:SS）
        temperature  : 温度值（浮点数，单位 ℃）
        motor_speed  : 电机转速（整数，单位 RPM）

    返回：
        conn : sqlite3.Connection 对象，供后续写入操作复用
    """
    # 连接数据库文件，若文件不存在则自动创建
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # 建表：IF NOT EXISTS 确保重复运行不会报错，历史数据也不会丢失
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS history_data (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT    NOT NULL,
            temperature  REAL,
            motor_speed  INTEGER
        )
    """)
    conn.commit()
    print(f"{GREEN}[数据记录器] ✅ 数据库 '{DB_FILE}' 初始化完成，表 'history_data' 就绪{RESET}")
    return conn

# ── 3. MQTT 回调函数：连接成功 ────────────────────────────────────────────────
def on_connect(client, userdata, flags, rc, properties):
    """
    当客户端成功连接到 Broker 后自动触发。
    注意：使用 CallbackAPIVersion.VERSION2 时，回调签名为 5 个参数。

    参数：
        rc         : 连接结果对象，rc.is_failure 为 False 表示连接成功
        properties : MQTT 5.0 属性（本项目未使用，但签名必须包含）
    """
    if not rc.is_failure:
        # 连接成功后自动订阅目标主题
        client.subscribe(MQTT_TOPIC)
        print(f"{GREEN}[数据记录器] ✅ 已连接 MQTT Broker ({MQTT_BROKER}:{MQTT_PORT}){RESET}")
        print(f"{GREEN}[数据记录器] 📡 已订阅主题: {MQTT_TOPIC}{RESET}")
    else:
        print(f"{RED}[数据记录器] ❌ MQTT 连接失败: {rc}{RESET}")

# ── 4. MQTT 回调函数：收到消息 ────────────────────────────────────────────────
def on_message(client, userdata, msg):
    """
    当订阅的主题收到新消息时自动触发。

    处理流程：
        1) 将 MQTT 消息负载（bytes）解码为 JSON 字典；
        2) 从 JSON 中提取 temperature 和 motor_speed；
        3) 连同当前时间戳，INSERT 到 SQLite 数据库；
        4) 在终端打印绿色确认日志。
    """
    try:
        # ── 4a. 解析 JSON 数据 ───────────────────────────────────────────────
        # 边缘网关发布的 payload 结构：
        # {
        #   "timestamp": "...",
        #   "source": "...",
        #   "data": {
        #       "temperature": 25.3,
        #       "motor_speed": 50
        #   },
        #   "raw_registers": { ... }
        # }
        payload = json.loads(msg.payload.decode("utf-8"))
        temperature = payload["data"]["temperature"]   # 温度，单位 ℃
        motor_speed = payload["data"]["motor_speed"]   # 电机转速，单位 RPM

        # ── 4b. 写入数据库 ───────────────────────────────────────────────────
        # 使用当前本地时间作为入库时间戳（与边缘网关的采集时间可能有微小差异）
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 参数化查询（? 占位符），防止 SQL 注入
        conn.execute(
            "INSERT INTO history_data (timestamp, temperature, motor_speed) VALUES (?, ?, ?)",
            (now, temperature, motor_speed)
        )
        conn.commit()  # 立即提交，确保数据不会因程序意外退出而丢失

        # ── 4c. 打印绿色日志，方便调试 ───────────────────────────────────────
        print(
            f"{GREEN}[数据记录器] 💾 已存入数据库 | "
            f"时间: {now} | 温度: {temperature}℃ | 转速: {motor_speed} RPM{RESET}"
        )

    except json.JSONDecodeError as e:
        # JSON 格式异常（边缘网关发送了非法数据）
        print(f"{RED}[数据记录器] ❌ JSON 解析失败: {e}{RESET}")
    except KeyError as e:
        # JSON 结构与预期不符（缺少必要字段）
        print(f"{RED}[数据记录器] ❌ 数据字段缺失: {e}{RESET}")
    except sqlite3.Error as e:
        # 数据库写入异常
        print(f"{RED}[数据记录器] ❌ 数据库写入失败: {e}{RESET}")

# ── 5. 主入口 ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  MQTT 数据持久化记录器")
    print("  数据链路：虚拟 PLC → 边缘网关 → 数据记录器（本脚本）")
    print("=" * 60)

    # 初始化数据库，获取全局连接对象
    conn = init_db()

    # 创建 MQTT 客户端，使用 CallbackAPIVersion.VERSION2 避免版本警告
    client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)

    # 注册回调函数
    client.on_connect = on_connect    # 连接成功时触发
    client.on_message = on_message    # 收到消息时触发

    try:
        # 连接到本地 MQTT Broker
        client.connect(MQTT_BROKER, MQTT_PORT, keepalive=60)

        print(f"{GREEN}[数据记录器] 🔄 正在监听数据，按 Ctrl+C 停止...{RESET}")

        # 阻塞式运行：持续监听消息，内部自动处理心跳与重连
        client.loop_forever()

    except KeyboardInterrupt:
        print(f"\n{GREEN}[数据记录器] ⏹️ 收到中断信号，正在关闭...{RESET}")

    except Exception as e:
        print(f"{RED}[数据记录器] ❌ 运行异常: {e}{RESET}")

    finally:
        # 安全释放所有资源
        client.disconnect()
        print("[数据记录器] MQTT 连接已断开")

        conn.close()
        print("[数据记录器] 数据库连接已关闭")

        print(f"{GREEN}[数据记录器] ✅ 数据记录器已安全退出{RESET}")

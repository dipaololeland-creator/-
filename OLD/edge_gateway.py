# 依赖版本：pymodbus 3.11.4 / paho-mqtt 2.1.0
# 功能：边缘网关程序。
#       每隔 2 秒通过 Modbus TCP 读取虚拟 PLC 的寄存器数据，
#       将结果组装为 JSON 格式，通过 MQTT 发布到消息代理。

# ── 1. 导入所需库 ────────────────────────────────────────────────────────────
from pymodbus.client import ModbusTcpClient        # Modbus TCP 同步客户端
from pymodbus.exceptions import ModbusException    # Modbus 协议层异常
import paho.mqtt.client as mqtt                    # MQTT 客户端
import json                                        # JSON 序列化
import time                                        # 定时休眠
import logging                                     # 日志输出

# ── 日志配置 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [边缘网关] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── 2. MQTT 配置并建立连接 ────────────────────────────────────────────────────
MQTT_BROKER  = "127.0.0.1"   # MQTT Broker 地址（本机）
MQTT_PORT    = 1883           # MQTT 默认端口
MQTT_TOPIC   = "factory/plc/data"  # 数据发布主题
MQTT_KEEPALIVE = 60           # 心跳保活间隔（秒）

mqtt_client = mqtt.Client()

try:
    mqtt_client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
    # 启动后台网络循环，处理心跳与回调（不阻塞主线程）
    mqtt_client.loop_start()
    logger.info(f"✅ MQTT 已连接至 {MQTT_BROKER}:{MQTT_PORT}")
except Exception as e:
    logger.error(f"❌ MQTT 连接失败: {e}")
    raise SystemExit(1)   # 无法建立 MQTT 连接时终止程序

# ── 3. Modbus TCP 配置并建立连接 ──────────────────────────────────────────────
MODBUS_HOST    = "127.0.0.1"  # 虚拟 PLC 地址（本机）
MODBUS_PORT    = 5020         # 虚拟 PLC 监听端口
MODBUS_TIMEOUT = 5            # 连接超时（秒）

modbus_client = ModbusTcpClient(
    host=MODBUS_HOST,
    port=MODBUS_PORT,
    timeout=MODBUS_TIMEOUT
)

if modbus_client.connect():
    logger.info(f"✅ Modbus TCP 已连接至 {MODBUS_HOST}:{MODBUS_PORT}")
else:
    logger.error("❌ Modbus TCP 连接失败，请确认虚拟 PLC 已启动")
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    raise SystemExit(1)   # 无法连接 PLC 时终止程序

# ── 4. 主扫描循环 ─────────────────────────────────────────────────────────────
# 模拟 PLC 扫描周期：每隔 2 秒读取一次现场数据并上报
logger.info(f"🔄 边缘网关启动，扫描周期 2 秒，发布主题: {MQTT_TOPIC}")
logger.info("按 Ctrl+C 停止网关")

try:
    while True:
        # ── 4a. 通过 Modbus 功能码 03 读取保持寄存器 ──────────────────────────
        # 起始地址 0，连续读取 2 个寄存器：
        #   偏移 0 (40001)：温度，存储值 = 实际温度(℃) × 10
        #   偏移 1 (40002)：电机速度，单位 RPM
        try:
            response = modbus_client.read_holding_registers(
                address=0,      # 起始寄存器偏移（0-based，对应 Modbus 地址 40001）
                count=2,        # 连续读取 2 个寄存器
                device_id=0     # 从站设备 ID（pymodbus 3.x 使用 device_id 关键字）
            )

            # 检查 Modbus 响应是否包含错误码
            if response.isError():
                logger.error(f"Modbus 读取返回错误: {response}")
                time.sleep(2)
                continue

        except ModbusException as e:
            logger.error(f"Modbus 协议层异常: {e}")
            time.sleep(2)
            continue
        except Exception as e:
            logger.error(f"读取寄存器时发生未知错误: {e}")
            time.sleep(2)
            continue

        # 从寄存器原始值还原工程量
        temperature_raw = response.registers[0]   # 原始值，需 ÷10 还原
        motor_speed_raw = response.registers[1]   # 原始值即实际转速（RPM）

        temperature = round(temperature_raw / 10.0, 1)  # 还原为实际温度（℃）
        motor_speed = motor_speed_raw                    # 转速无需换算

        # ── 4b. 将读取结果组装为 JSON 字典 ──────────────────────────────────
        payload = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),   # 采集时间戳
            "source":    f"{MODBUS_HOST}:{MODBUS_PORT}",        # 数据来源标识
            "data": {
                "temperature": temperature,   # 温度，单位 ℃
                "motor_speed": motor_speed    # 电机速度，单位 RPM
            },
            "raw_registers": {               # 保留原始寄存器值，便于核查
                "40001": temperature_raw,
                "40002": motor_speed_raw
            }
        }

        # ── 4c. 将 JSON 数据发布至 MQTT 主题 ─────────────────────────────────
        message = json.dumps(payload, ensure_ascii=False)
        result = mqtt_client.publish(MQTT_TOPIC, message)

        if result.rc == mqtt.MQTT_ERR_SUCCESS:
            logger.info(f"📤 已发布 → {MQTT_TOPIC} | 温度: {temperature}℃ | 转速: {motor_speed} RPM")
        else:
            logger.warning(f"MQTT 发布失败，错误码: {result.rc}")

        # 等待下一个扫描周期（2 秒）
        time.sleep(2)

except KeyboardInterrupt:
    logger.info("\n⏹️ 收到中断信号，正在关闭边缘网关...")

finally:
    # 安全释放所有资源
    modbus_client.close()
    logger.info("Modbus 连接已关闭")

    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    logger.info("MQTT 连接已断开")

    logger.info("✅ 边缘网关已安全退出")

# 依赖版本：python-snap7 1.4.1 / paho-mqtt 2.1.0
# 功能：边缘网关程序（Snap7 版本）。
#       每隔 2 秒通过 S7 协议直接读取西门子 S7-1200 的 DB 块数据，
#       将结果组装为 JSON 格式，通过 MQTT 发布到消息代理。
#
# 与 edge_gateway.py（Modbus 版）的核心区别：
#       Modbus 版 → 通过 Modbus TCP 功能码 03 读保持寄存器，通用但需要做寄存器地址映射
#       Snap7 版  → 通过 S7 协议直接读 PLC 的 DB 块，原生支持西门子数据类型（Bool/Real/Int）
#
# DB1 数据块结构（需在 TIA Portal 中提前组态）：
#   ┌──────────┬────────────┬──────────┬──────────────────────────────────┐
#   │ 偏移量    │ 变量名      │ 数据类型  │ 说明                              │
#   ├──────────┼────────────┼──────────┼──────────────────────────────────┤
#   │ 0.0      │ Motor_Status│ Bool     │ 电机运行状态：FALSE=停止 TRUE=运行 │
#   │ 2.0      │ Motor_Speed │ Int      │ 电机转速，单位 RPM（2 字节整数）    │
#   │ 4.0      │ Temperature │ Real     │ 环境温度，单位 ℃（4 字节浮点数）    │
#   │ 8.0      │ Alarm_Code  │ Int      │ 报警码：0=无报警，>0=对应故障编号    │
#   └──────────┴────────────┴──────────┴──────────────────────────────────┘
#   总计需读取：偏移 0 ~ 偏移 9，共 10 个字节
#
#   优化策略："大块读取 + 本地解包"
#   仅发起一次 db_read(1, 0, 10) 就把整个 DB1 的 10 字节 payload 全部拉回本地，
#   再用 snap7.util 在内存中按偏移量切割解析，把网络 I/O 降到最低。
#
#   地址对齐说明：
#   Bool 占 1 bit，但 PLC 自动将后续 Int 对齐到偶数字节（偏移 2）。
#   Real（4 字节 IEEE 754）同样从偶数偏移量 4 开始，满足 S7 的 2 字节对齐要求。

# ── 1. 导入所需库 ────────────────────────────────────────────────────────────
import paho.mqtt.client as mqtt                    # MQTT 客户端
from paho.mqtt.client import CallbackAPIVersion    # 回调 API 版本枚举（paho-mqtt 2.x）
import snap7                                       # Snap7 S7 通信库
import snap7.util                                  # Snap7 数据解析工具（get_bool/get_real/get_int）
import struct                                      # 二进制数据打包/解包（备用）
import time                                        # 定时休眠
import json                                        # JSON 序列化
import logging                                     # 日志输出

# ── ANSI 终端颜色代码 ────────────────────────────────────────────────────────
GREEN = "\033[32m"
RED   = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"

# ── 日志配置 ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [边缘网关-Snap7] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── 2. MQTT 配置 ─────────────────────────────────────────────────────────────
MQTT_BROKER    = "127.0.0.1"          # MQTT Broker 地址（本机）
MQTT_PORT      = 1883                 # MQTT 默认端口
MQTT_TOPIC     = "factory/plc/data"   # 数据发布主题（与 Modbus 版保持一致，下游订阅者无需修改）
MQTT_KEEPALIVE = 60                   # 心跳保活间隔（秒）

# ── 3. Snap7 PLC 连接配置 ────────────────────────────────────────────────────
# 这组参数相当于在 WinCC 中配置 S7 连接时填写的"PLC 站点信息"
PLC_IP   = "192.168.74.128"   # PLC 的 IP 地址（通过 NetToPLCsim 桥接到本机仿真器）
PLC_RACK = 0               # 机架号：S7-1200/1500 固定为 0
PLC_SLOT = 1               # 槽号：S7-1200 固定为 1（S7-300/400 通常为 2）

# DB 块读取参数
DB_NUMBER    = 1    # 读取 DB1 数据块
DB_OFFSET    = 0    # 起始偏移量（从第 0 个字节开始读）
DB_READ_SIZE = 10   # 一次性读取 10 个字节（覆盖 Motor_Status + Temperature + Motor_Speed + Alarm_Code）

# 扫描周期
SCAN_INTERVAL = 2   # 秒


# ── 辅助函数：建立 Snap7 连接（含重连机制）───────────────────────────────────
def connect_plc():
    """
    创建 Snap7 客户端并连接到 S7 PLC。

    连接过程等价于：
        1. WinCC 中"添加新连接" → 选择 S7 协议
        2. 填写 IP 地址、机架号、槽号
        3. 点击"测试连接"

    返回：
        plc_client : snap7.client.Client 实例（已连接）

    异常：
        连接失败时抛出 Exception，由调用方处理重试逻辑
    """
    plc_client = snap7.client.Client()
    try:
        plc_client.connect(PLC_IP, PLC_RACK, PLC_SLOT)
        if plc_client.get_connected():
            logger.info(f"{GREEN}✅ Snap7 已连接至 PLC {PLC_IP} (rack={PLC_RACK}, slot={PLC_SLOT}){RESET}")
            return plc_client
        else:
            raise Exception("connect() 返回但连接状态为 False")
    except Exception as e:
        logger.error(f"{RED}❌ Snap7 连接失败: {e}{RESET}")
        raise


def reconnect_plc(plc_client):
    """
    异常重连机制：当通信中断时，尝试重新连接 PLC。

    在真实产线上，PLC 可能因为以下原因短暂断联：
        - 网线被拔掉又重新插上
        - PLC 程序下载导致 CPU 重启
        - 交换机端口重置
    所以采集程序必须有自动重连能力，不能一断就崩。

    策略：每 5 秒重试一次，直到连接恢复。
    """
    while True:
        try:
            logger.info(f"{YELLOW}🔄 正在尝试重新连接 PLC {PLC_IP}...{RESET}")
            # 先断开旧连接（如果还保持着的话），再重新连
            try:
                plc_client.disconnect()
            except Exception:
                pass
            plc_client.connect(PLC_IP, PLC_RACK, PLC_SLOT)
            if plc_client.get_connected():
                logger.info(f"{GREEN}✅ PLC 重连成功{RESET}")
                return plc_client
        except Exception as e:
            logger.error(f"{RED}❌ 重连失败: {e}，5 秒后重试...{RESET}")
        time.sleep(5)


# ── 4. 主程序入口 ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 65)
    print("  边缘网关（Snap7 版）—— 直连西门子 S7-1200 DB 块")
    print("  协议：S7 Protocol（替代 Modbus TCP）")
    print("  策略：大块读取 db_read(1,0,10) + 本地解包（Block Read + Local Parse）")
    print("  目标：DB1 | Motor_Status(0.0) Motor_Speed(2.0) Temperature(4.0) Alarm_Code(8.0)")
    print("=" * 65)

    # ── 4a. 建立 MQTT 连接 ───────────────────────────────────────────────────
    # 使用 CallbackAPIVersion.VERSION2 避免 paho-mqtt 2.x 的版本警告
    mqtt_client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2)

    try:
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, MQTT_KEEPALIVE)
        mqtt_client.loop_start()   # 启动后台网络循环（处理心跳与回调，不阻塞主线程）
        logger.info(f"✅ MQTT 已连接至 {MQTT_BROKER}:{MQTT_PORT}")
    except Exception as e:
        logger.error(f"❌ MQTT 连接失败: {e}")
        raise SystemExit(1)

    # ── 4b. 建立 Snap7 PLC 连接 ─────────────────────────────────────────────
    try:
        plc_client = connect_plc()
    except Exception:
        logger.error("首次连接 PLC 失败，进入重连模式...")
        plc_client = snap7.client.Client()
        plc_client = reconnect_plc(plc_client)

    # ── 4c. 主扫描循环 ──────────────────────────────────────────────────────
    # 模拟 PLC 扫描周期：每隔 2 秒读取一次 DB 块数据并上报
    logger.info(f"🔄 边缘网关启动，扫描周期 {SCAN_INTERVAL} 秒，发布主题: {MQTT_TOPIC}")
    logger.info("按 Ctrl+C 停止网关")

    try:
        while True:
            try:
                # ── 步骤 1：大块读取 —— 一次 S7 请求拉回整个 DB1 数据块 ────
                # db_read(db_number, start, size)
                #   db_number = 1   → 读 DB1
                #   start     = 0   → 从偏移 0 开始
                #   size      = 10  → 一次性读取 10 个字节
                #
                # 核心优化：只发起一次网络 I/O，将 DB1 偏移 0~9 的完整二进制
                # payload 拉到本地内存，后续解包全部在 CPU 内完成，零额外通信开销。
                #
                # 返回值 raw_payload 是一个 bytearray，长度为 10：
                #   字节 [0]     → Motor_Status (Bool，取第 0 位)
                #   字节 [1]     → 对齐填充（PLC 自动插入，无实际数据）
                #   字节 [2~3]   → Motor_Speed  (Int, 16 位有符号整数, 大端序)
                #   字节 [4~7]   → Temperature  (Real, IEEE 754 单精度浮点, 大端序)
                #   字节 [8~9]   → Alarm_Code   (Int, 16 位有符号整数, 大端序)
                #
                # 对比 Modbus 版：Modbus 只能读"编号寄存器"（40001, 40002...），
                # 而 Snap7 可以直接按字节偏移量读 DB 块，数据类型更丰富。
                raw_payload = plc_client.db_read(DB_NUMBER, DB_OFFSET, DB_READ_SIZE)

                # ── 步骤 2：内存级解包 —— 按偏移量切割解析工程量 ──────────────
                # 所有解析均在本地内存中完成，不产生任何额外的 PLC 通信请求。

                # 2a) 解析 Motor_Status (Bool)
                # snap7.util.get_bool(buffer, byte_index, bit_index)
                # 从 raw_payload 的第 0 个字节、第 0 位取出布尔值
                # 等价于 TIA Portal 中的 DB1.DBX0.0
                motor_status = snap7.util.get_bool(raw_payload, 0, 0)

                # 2b) 解析 Motor_Speed (Int / 16-bit 有符号整数)
                # snap7.util.get_int(buffer, byte_index)
                # 从 raw_payload 的第 2 个字节开始，取 2 字节解析为有符号整数
                # 等价于 TIA Portal 中的 DB1.DBW2
                motor_speed = snap7.util.get_int(raw_payload, 2)

                # 2c) 解析 Temperature (Real / 32-bit 浮点数)
                # snap7.util.get_real(buffer, byte_index)
                # 从 raw_payload 的第 4 个字节开始，取 4 字节解析为 IEEE 754 浮点数
                # 等价于 TIA Portal 中的 DB1.DBD4
                # 注意：S7 使用大端序（Big-Endian），snap7.util 内部已处理字节序转换
                temperature = snap7.util.get_real(raw_payload, 4)
                temperature = round(temperature, 1)  # 保留一位小数

                # 2d) 解析 Alarm_Code (Int / 16-bit 有符号整数)
                # snap7.util.get_int(buffer, byte_index)
                # 从 raw_payload 的第 8 个字节开始，取 2 字节解析为有符号整数
                # 等价于 TIA Portal 中的 DB1.DBW8
                # 报警码含义：0 = 无报警，>0 = 对应故障编号（如 1=过温, 2=过载 等）
                alarm_code = snap7.util.get_int(raw_payload, 8)

                # ── 步骤 2x：解包结果日志 ── 证明内存级解包成功 ──────────────
                logger.info(
                    f"📦 解包完成 | raw={raw_payload.hex().upper()} → "
                    f"Motor_Status={motor_status}, Motor_Speed={motor_speed} RPM, "
                    f"Temperature={temperature} ℃, Alarm_Code={alarm_code}"
                )

            except Exception as e:
                # ── 通信异常处理 ─────────────────────────────────────────────
                # 读取失败可能是 PLC 断联、DB 块不存在、网络超时等原因
                # 对标真实工控场景：传感器断线时 WinCC 会显示"通信中断"报警
                logger.error(f"{RED}❌ DB 块读取失败: {e}{RESET}")
                logger.info("进入重连模式...")
                plc_client = reconnect_plc(plc_client)
                continue  # 重连成功后跳过本次循环，进入下一次扫描

            # ── 步骤 3：将解析结果组装为 JSON 字典 ───────────────────────────
            # payload 结构与 Modbus 版保持一致，确保下游的 data_recorder.py
            # 和 DataCollector2.py 无需修改任何代码即可正常工作
            payload = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),  # 采集时间戳
                "source": f"{PLC_IP} (S7-1200 DB{DB_NUMBER})",    # 数据来源标识
                "data": {
                    "temperature": temperature,    # 温度，单位 ℃
                    "motor_speed": motor_speed     # 电机转速，单位 RPM
                },
                "alarm_code": alarm_code,            # 报警码：0=正常，>0=故障编号
                "motor_status": motor_status,         # 电机运行状态（Snap7 版新增字段）
                "raw_bytes": raw_payload.hex().upper()  # 保留原始字节（十六进制），便于调试核查
            }

            # ── 步骤 4：将 JSON 数据发布至 MQTT 主题 ─────────────────────────
            message = json.dumps(payload, ensure_ascii=False)
            result = mqtt_client.publish(MQTT_TOPIC, message)

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                # 发布成功：打印绿色日志，方便在终端实时观察数据流
                status_text = "运行" if motor_status else "停止"
                print(
                    f"{GREEN}[Snap7 网关] 📤 已发布 → {MQTT_TOPIC} | "
                    f"电机: {status_text} | "
                    f"温度: {temperature}℃ | "
                    f"转速: {motor_speed} RPM | "
                    f"报警码: {alarm_code}{RESET}"
                )
                # 如果报警码大于 0，额外打印红色警告提示
                if alarm_code > 0:
                    print(
                        f"{RED}⚠️  [报警] 检测到活动报警！报警码: {alarm_code} "
                        f"—— 请检查现场设备状态{RESET}"
                    )
            else:
                logger.warning(f"MQTT 发布失败，错误码: {result.rc}")

            # 等待下一个扫描周期
            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        logger.info(f"\n⏹️ 收到中断信号，正在关闭边缘网关...")

    finally:
        # ── 安全释放所有资源 ─────────────────────────────────────────────────
        # 相当于 WinCC 退出时"断开所有 PLC 连接"
        try:
            plc_client.disconnect()
            logger.info("Snap7 PLC 连接已断开")
        except Exception:
            pass

        mqtt_client.loop_stop()
        mqtt_client.disconnect()
        logger.info("MQTT 连接已断开")

        logger.info(f"{GREEN}✅ 边缘网关（Snap7 版）已安全退出{RESET}")

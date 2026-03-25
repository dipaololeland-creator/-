# 依赖版本：pymodbus 3.11.4 / paho-mqtt 2.1.0
# 功能：通过 Modbus TCP 协议从虚拟 PLC 周期性采集现场数据，
#       并将结构化结果通过 MQTT 协议上报至消息代理（Broker）。

from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException, ConnectionException
import paho.mqtt.client as mqtt
import time
import json
import logging
import threading


class DataCollector:
    """
    工业现场数据采集器。

    职责：
        1. 通过 Modbus TCP 协议与虚拟 PLC 建立连接并读写寄存器；
        2. 将原始寄存器值解码为工程量（温度、湿度、电机参数）；
        3. 将采集结果封装为 JSON 格式，通过 MQTT 发布至指定主题；
        4. 支持后台守护线程模式（非阻塞）和上下文管理器（with 语句）两种使用方式。

    寄存器地址映射（Modbus 保持寄存器，功能码 0x03）：
        40001 (偏移 0)：环境温度，原始值 = 实际值 × 10，单位 0.1℃
        40002 (偏移 1)：环境湿度，原始值 = 实际值 × 10，单位 0.1%RH
        40003 (偏移 2)：电机运行状态，0 = 停止，1 = 运行
        40004 (偏移 3)：电机转速设定值，单位 RPM
        40005 (偏移 4)：电机运行电流，单位 0.1A
    """

    def __init__(self, host='127.0.0.1', port=5020, timeout=5):
        """
        初始化采集器，建立 MQTT 连接并配置日志系统。

        Args:
            host    (str): 目标 PLC 的 IP 地址，默认为本机回环地址。
            port    (int): Modbus TCP 端口号，默认 5020（虚拟 PLC 监听端口）。
            timeout (int): TCP 连接超时时间，单位秒，默认 5 秒。

        说明：
            MQTT 连接在构造阶段尝试建立。若 Broker 未启动，
            self.mqtt_client 将被置为 None，采集器以离线模式运行，
            数据仍可正常采集，仅跳过 MQTT 上报步骤。
        """
        self.host = host
        self.port = port
        self.timeout = timeout

        # Modbus 客户端实例，调用 connect() 后初始化
        self.client = None
        # 标志位：记录最近一次 connect() 的返回结果，供部分路径快速判断
        self.connected = False

        # 初始化 MQTT 客户端并尝试连接本地 Broker
        # 若 Broker 不可用，置为 None 以启用离线模式，避免阻塞程序启动
        self.mqtt_client = mqtt.Client()
        try:
            self.mqtt_client.connect("localhost", 1883, 60)
        except Exception as e:
            self.mqtt_client = None
            # 此时 logger 尚未初始化，使用 print 输出早期警告
            print(f"[WARNING] MQTT 初始连接失败，将以离线模式运行: {e}")

        # 后台采集线程句柄及其停止信号
        self._service_thread = None
        self._stop_event = threading.Event()

        # 配置统一日志格式，包含时间戳、模块名和日志级别
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def connect(self):
        """
        创建 Modbus TCP 客户端并建立与 PLC 的连接。

        Returns:
            bool: 连接成功返回 True，否则返回 False。

        说明：
            每次调用均会重新创建客户端实例并尝试握手，
            可用于初次连接或手动重连场景。
        """
        try:
            self.client = ModbusTcpClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout
            )

            self.connected = self.client.connect()
            if self.connected:
                self.logger.info(f"✅ 成功连接到虚拟PLC {self.host}:{self.port}")
            else:
                self.logger.error("❌ 连接失败，请检查虚拟PLC是否运行")

            return self.connected

        except ConnectionException as e:
            self.logger.error(f"❌ Modbus 连接异常: {e}")
            return False
        except Exception as e:
            self.logger.error(f"❌ 连接时发生未知错误: {e}")
            return False

    def is_connected(self):
        """
        实时检测当前 TCP 连接是否有效。

        Returns:
            bool: Socket 处于打开状态返回 True，否则返回 False。

        说明：
            pymodbus 3.x 使用 is_socket_open() 检测底层 socket 状态，
            比依赖 self.connected 标志位更能反映真实连接情况，
            可检测到运行期间的网络中断。
        """
        if self.client:
            return self.client.is_socket_open()
        return False

    def start_service(self, interval=5, use_mqtt=True):
        """
        在后台守护线程中启动周期性采集服务（不阻塞主线程）。

        Args:
            interval (int):  采集周期，单位秒，默认 5 秒。
            use_mqtt (bool): 是否将采集结果通过 MQTT 发布，默认开启。

        说明：
            服务线程以 daemon=True 启动，主程序退出时线程自动终止。
            重复调用时若服务已在运行，将输出警告并直接返回，不会创建重复线程。
            可通过 stop_service() 随时安全停止。
        """
        if self._service_thread and self._service_thread.is_alive():
            self.logger.warning("后台采集服务已在运行，忽略重复启动请求")
            return

        self._stop_event.clear()
        self._service_thread = threading.Thread(
            target=self._service_loop,
            args=(interval, use_mqtt),
            daemon=True
        )
        self._service_thread.start()
        self.logger.info(f"后台采集服务已启动，采集间隔 {interval} 秒，MQTT 发布: {use_mqtt}")

    def stop_service(self):
        """
        发送停止信号并等待后台采集线程安全退出。

        说明：
            通过 threading.Event 通知服务循环退出，最长等待 10 秒。
            此方法在 close() 中自动调用，通常无需手动调用。
        """
        if self._service_thread and self._service_thread.is_alive():
            self._stop_event.set()
            self._service_thread.join(timeout=10)
            self.logger.info("后台采集服务已停止")
        else:
            self.logger.info("后台采集服务当前未运行")

    def _service_loop(self, interval, use_mqtt):
        """
        后台采集线程的主循环（内部方法，不对外暴露）。

        每轮执行一次完整采集，若启用 MQTT 则通过 mqtt_publish() 发布结果。
        单轮异常不会中断整个服务，异常信息记录至日志后继续下一轮循环。
        使用 Event.wait() 代替 time.sleep()，以支持在等待期间被立即唤醒并退出。

        Args:
            interval (int):  两次采集之间的等待时长，单位秒。
            use_mqtt (bool): 是否发布 MQTT 消息。
        """
        while not self._stop_event.is_set():
            try:
                data = self.collect_one_round()
                if data and use_mqtt:
                    self.mqtt_publish(data)
            except Exception as e:
                self.logger.error(f"采集服务异常（已跳过本轮）: {e}")
            # Event.wait() 在收到停止信号时可提前唤醒，优于固定 time.sleep()
            self._stop_event.wait(timeout=interval)

    def mqtt_publish(self, data):
        """
        将采集结果序列化为 JSON 字符串并发布至 MQTT 主题。

        Args:
            data (dict): 由 collect_one_round() 返回的结构化数据包。

        说明：
            发布主题固定为 "warehouse/data"。
            若 MQTT 客户端未初始化（离线模式），记录警告并跳过，不抛出异常。
        """
        try:
            if self.mqtt_client:
                message = json.dumps(data, ensure_ascii=False)
                self.mqtt_client.publish("warehouse/data", message)
                self.logger.debug(f"MQTT 消息已发布至 warehouse/data")
            else:
                self.logger.warning("MQTT 客户端未初始化（离线模式），跳过本次发布")
        except Exception as e:
            self.logger.error(f"MQTT 发布失败: {e}")

    def read_holding_registers(self, address, count, slave_id=0):
        """
        读取 PLC 保持寄存器（Modbus 功能码 0x03）。

        Args:
            address  (int): 寄存器起始地址，0-based（0 对应 Modbus 地址 40001）。
            count    (int): 连续读取的寄存器数量。
            slave_id (int): 从站设备 ID，默认为 0。

        Returns:
            list | None: 成功返回寄存器值列表，读取失败或异常返回 None。

        说明：
            调用前会实时检查 socket 连接状态；若已断开，自动尝试重连一次。
            pymodbus 3.x 使用 device_id 关键字（旧版为 unit）。
        """
        try:
            # 连接检测：优先使用实时 socket 状态，断线时自动尝试重连
            if not self.is_connected():
                self.logger.warning("连接已断开，尝试自动重连...")
                if not self.connect():
                    return None

            response = self.client.read_holding_registers(
                address=address,
                count=count,
                device_id=slave_id   # pymodbus 3.x 关键字参数名为 device_id
            )

            # isError() 是 pymodbus 3.x 统一的响应错误判断接口
            if response.isError():
                self.logger.error(f"读取寄存器返回错误响应: {response}")
                return None

            return response.registers

        except ModbusException as e:
            self.logger.error(f"Modbus 协议层异常: {e}")
            return None
        except Exception as e:
            self.logger.error(f"读取寄存器时发生未知错误: {e}")
            return None

    def write_single_register(self, address, value, slave_id=0):
        """
        向单个保持寄存器写入值（Modbus 功能码 0x06）。

        Args:
            address  (int): 目标寄存器地址，0-based（0 对应 Modbus 地址 40001）。
            value    (int): 写入值，有效范围 0~65535（16 位无符号整数）。
            slave_id (int): 从站设备 ID，默认为 0。

        Returns:
            bool: 写入成功返回 True，否则返回 False。
        """
        try:
            if not self.is_connected():
                self.logger.warning("连接已断开，尝试自动重连...")
                if not self.connect():
                    return False

            response = self.client.write_register(
                address=address,
                value=value,
                device_id=slave_id
            )

            if response.isError():
                self.logger.error(f"写入寄存器返回错误响应: {response}")
                return False

            # 输出时转换为 Modbus 标准地址（40001 起）便于核对
            self.logger.info(f"写入成功：Modbus 地址 {address + 40001} = {value}")
            return True

        except ModbusException as e:
            self.logger.error(f"Modbus 协议层写入异常: {e}")
            return False

    def decode_temperature(self, raw_value):
        """
        将温度寄存器原始值还原为实际温度。

        PLC 端将温度值乘以 10 后存入寄存器（避免浮点传输），
        此处除以 10 还原，精度为 0.1℃。

        Args:
            raw_value (int): 寄存器原始整数值。

        Returns:
            float: 实际温度值，单位℃。
        """
        return raw_value / 10.0

    def decode_humidity(self, raw_value):
        """
        将湿度寄存器原始值还原为实际相对湿度。

        编码规则与温度相同：原始值 = 实际值 × 10，精度为 0.1%RH。

        Args:
            raw_value (int): 寄存器原始整数值。

        Returns:
            float: 实际相对湿度值，单位 %RH。
        """
        return raw_value / 10.0

    def collect_one_round(self):
        """
        执行一轮完整的数据采集：读取寄存器 → 解码 → 封装数据包。

        一次性批量读取 5 个连续寄存器（地址 0~4，对应 40001~40005），
        减少 Modbus 通信次数，提升采集效率。

        Returns:
            dict | None: 采集成功返回结构化数据包，读取失败返回 None。

        数据包结构：
            {
                "timestamp": "YYYY-MM-DD HH:MM:SS",
                "sensors": {"temperature": float, "humidity": float},
                "motor":   {"status": str, "speed_setting": int, "current": int},
                "raw_registers": {"40001": int, ..., "40005": int}  # 保留原始值供核查
            }
        """
        # 批量读取 5 个寄存器，覆盖温度、湿度、电机状态、转速、电流
        data = self.read_holding_registers(address=0, count=5)

        if not data or len(data) < 5:
            self.logger.error("读取数据不完整，本轮采集跳过")
            return None

        # 按寄存器偏移量拆分原始值
        temperature_raw   = data[0]
        humidity_raw      = data[1]
        motor_status_raw  = data[2]
        speed_setting_raw = data[3]
        motor_current_raw = data[4]

        # 将原始值解码为工程量
        temperature  = self.decode_temperature(temperature_raw)
        humidity     = self.decode_humidity(humidity_raw)
        motor_status = "运行" if motor_status_raw == 1 else "停止"
        speed_setting = speed_setting_raw
        motor_current = motor_current_raw

        # 构建上报数据包，raw_registers 字段保留原始值便于后续数据核查与调试
        data_packet = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sensors": {
                "temperature": round(temperature, 1),
                "humidity":    round(humidity, 1)
            },
            "motor": {
                "status":        motor_status,
                "speed_setting": speed_setting,
                "current":       motor_current
            },
            "raw_registers": {
                "40001": temperature_raw,
                "40002": humidity_raw,
                "40003": motor_status_raw,
                "40004": speed_setting_raw,
                "40005": motor_current_raw
            }
        }

        self.logger.info("📊 本轮采集结果:\n" + json.dumps(data_packet, indent=2, ensure_ascii=False))

        # MQTT 发布由调用方（_service_loop → mqtt_publish）统一负责，此处仅返回数据包
        return data_packet

    def run_continuous(self, interval=5):
        """
        以阻塞方式持续采集数据（适用于独立脚本场景）。

        每隔 interval 秒执行一次 collect_one_round()，直到用户按 Ctrl+C 中断。
        退出时自动调用 close() 关闭连接。

        Args:
            interval (int): 采集间隔，单位秒，默认 5 秒。

        说明：
            此方法会阻塞当前线程。若需在程序中集成后台采集，
            请改用 start_service() / stop_service() 非阻塞方式。
        """
        # 使用实时 socket 状态检测，避免因标志位滞后而误判连接有效
        if not self.is_connected():
            self.logger.error("当前未连接，请先调用 connect()")
            return

        self.logger.info(f"🔄 开始持续采集，间隔 {interval} 秒...")
        self.logger.info("按 Ctrl+C 停止采集")

        try:
            while True:
                self.collect_one_round()
                time.sleep(interval)
        except KeyboardInterrupt:
            self.logger.info("\n⏹️ 用户中断，采集已停止")
        finally:
            self.close()

    def close(self):
        """
        停止后台服务并关闭 Modbus TCP 连接。

        说明：
            先尝试停止后台服务线程（stop_service），再关闭 socket 连接。
            stop_service 的异常被静默忽略，确保 close() 始终能完成连接关闭。
        """
        try:
            self.stop_service()
        except Exception:
            pass  # stop_service 失败不应阻止后续关闭动作

        if self.client:
            self.client.close()
            self.connected = False
            self.logger.info("Modbus 连接已关闭")

    def __enter__(self):
        """
        实现上下文管理器入口，自动建立连接。

        使用 with DataCollector() as collector: 语法时自动调用。
        """
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        实现上下文管理器出口，退出 with 块时自动关闭连接。

        无论 with 块内是否发生异常，均保证资源被正确释放。
        """
        self.close()


# ==================== 功能测试函数 ====================
# 以下函数用于验证各核心功能，可单独调用，也可在调试时直接运行。

def test_single_read():
    """验证单次数据采集流程是否正常。"""
    print("\n" + "="*50)
    print("测试 1：单次读取")
    print("="*50)

    collector = DataCollector()
    if collector.connect():
        collector.collect_one_round()
        collector.close()
    else:
        print("❌ 连接失败，请确保虚拟PLC已启动")


def test_continuous_read():
    """验证持续采集模式，按 Ctrl+C 退出。"""
    print("\n" + "="*50)
    print("测试 2：持续读取（按 Ctrl+C 停止）")
    print("="*50)

    collector = DataCollector()
    if collector.connect():
        collector.run_continuous(interval=3)
    else:
        print("❌ 连接失败，请确保虚拟PLC已启动")


def test_write_operation():
    """验证寄存器写入功能：依次发送启动和停止指令，并在操作前后读取状态。"""
    print("\n" + "="*50)
    print("测试 3：写入电机控制指令")
    print("="*50)

    collector = DataCollector()
    if collector.connect():
        print("写入前状态：")
        collector.collect_one_round()

        # 向地址 40003（偏移量 2）写入 1，触发电机启动
        print("\n发送启动指令（40003 = 1）...")
        success = collector.write_single_register(address=2, value=1)

        if success:
            time.sleep(1)
            print("\n启动后状态：")
            collector.collect_one_round()

            # 向地址 40003（偏移量 2）写入 0，触发电机停止
            print("\n发送停止指令（40003 = 0）...")
            collector.write_single_register(address=2, value=0)
            time.sleep(1)
            print("\n停止后状态：")
            collector.collect_one_round()

        collector.close()
    else:
        print("❌ 连接失败，请确保虚拟PLC已启动")


def test_context_manager():
    """验证上下文管理器（with 语句）能否自动管理连接生命周期。"""
    print("\n" + "="*50)
    print("测试 4：上下文管理器（推荐使用方式）")
    print("="*50)

    # with 语句自动调用 __enter__（建立连接）和 __exit__（关闭连接）
    with DataCollector() as collector:
        for i in range(5):
            print(f"\n第 {i+1} 次采集：")
            collector.collect_one_round()
            time.sleep(1)
    # 退出 with 块时连接已自动关闭，无需手动调用 close()


if __name__ == "__main__":
    print("🚀 数据采集器启动 - pymodbus 3.11.4")
    print("请确保虚拟PLC (virtual_plc.py) 已在另一个终端运行")
    print("后台服务每 5 秒采集一次，按 Ctrl+C 停止\n")

    # 提前声明为 None，防止构造函数抛出异常时 finally 块因变量未定义而产生二次错误
    collector = None
    try:
        collector = DataCollector()
        if collector.connect():
            print("✅ 已成功连接到PLC")
            print("正在启动后台采集服务...\n")
            # 启动非阻塞后台服务，主线程保持运行以响应 Ctrl+C
            collector.start_service(interval=5, use_mqtt=True)

            print("服务运行中，按 Ctrl+C 停止...")
            while True:
                time.sleep(1)
        else:
            print("❌ 无法连接到PLC，请检查虚拟PLC是否正常运行")

    except KeyboardInterrupt:
        print("\n\n⏹️ 收到中断信号，正在停止采集服务...")
    finally:
        if collector is not None:
            try:
                collector.stop_service()
                collector.close()
                print("✅ 采集器已安全关闭")
            except Exception as e:
                print(f"关闭过程中发生错误: {e}")
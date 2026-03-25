# 依赖版本：pymodbus 3.11.4
# 功能：模拟一台 PLC，通过 Modbus TCP 协议对外提供寄存器读写服务。

import asyncio
import random
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusDeviceContext,
    ModbusServerContext,
)
from pymodbus.server import StartAsyncTcpServer


class VirtualPLC:
    """
    虚拟 PLC 服务端。

    职责：
        1. 初始化 Modbus 保持寄存器数据块，并写入默认初值；
        2. 通过后台协程周期性模拟现场数据变化（温度随机波动、电机电流联动）；
        3. 启动 Modbus TCP Server，监听来自采集器的读写请求。

    寄存器地址映射（保持寄存器，功能码 0x03/0x06）：
        偏移 0 (40001)：环境温度，存储值 = 实际温度(℃) × 10，例如 250 表示 25.0℃
        偏移 1 (40002)：环境湿度，存储值 = 实际湿度(%RH) × 10（当前版本固定不变）
        偏移 2 (40003)：电机运行状态，0 = 停止，1 = 运行（可由采集器写入控制）
        偏移 3 (40004)：电机转速设定值，单位 RPM（当前版本固定不变）
        偏移 4 (40005)：电机运行电流，电机运行时随机模拟，停止时为 0

    网络配置：
        监听地址：0.0.0.0（接受任意来源连接）
        监听端口：5020
        协议：Modbus TCP（异步模式）
    """

    def __init__(self):
        """
        初始化 Modbus 数据存储并调用寄存器初始化。

        实现说明：
            pymodbus 3.x 中，写寄存器须通过 ModbusDeviceContext 实例操作，
            ServerContext 本身不直接暴露寄存器写接口，因此需单独持有 device 引用。
        """
        # 创建保持寄存器数据块：起始地址 0，预分配 100 个寄存器，初值全为 0
        hr_block = ModbusSequentialDataBlock(0, [0] * 100)

        # 创建单设备上下文，hr 参数绑定保持寄存器数据块
        device = ModbusDeviceContext(hr=hr_block)

        # 将设备注册到服务器上下文（single=True 表示单从站模式）
        self.context = ModbusServerContext(devices=device, single=True)

        # 保存 device 引用，用于在服务运行期间直接读写寄存器
        # pymodbus 3.x 中必须通过 device 而非 context 操作寄存器数据
        self.device = device

        self._init_registers()

    def _init_registers(self):
        """
        向保持寄存器写入初始默认值，模拟 PLC 上电后的初始现场状态。

        说明：
            setValues(功能码, 起始偏移, 值列表)
            功能码 3 对应保持寄存器（Holding Registers），与采集端读取功能码一致。
        """
        self.device.setValues(3, 0, [250])   # 温度初值：250 → 25.0℃
        self.device.setValues(3, 1, [600])   # 湿度初值：600 → 60.0%RH
        self.device.setValues(3, 2, [0])     # 电机状态初值：0 = 停止
        self.device.setValues(3, 3, [50])    # 转速设定初值：50 RPM
        self.device.setValues(3, 4, [0])     # 电机电流初值：0（停止时无电流）

        print("[虚拟PLC] 寄存器初始化完成")

    async def update_sensor_data(self):
        """
        周期性模拟现场数据变化的后台协程（每 3 秒更新一次）。

        模拟逻辑：
            - 温度：在 25.0℃ 基础上叠加 ±1℃ 随机波动，模拟真实环境扰动；
            - 电机电流：当电机状态寄存器（偏移 2）为 1（运行）时，
              随机生成 10.0~20.0A 范围内的电流值（存储值 × 10），
              停止时电流保持为 0；
            - 湿度和转速设定在本版本中不做周期更新，维持初始值。

        说明：
            电机状态寄存器由采集端（DataCollector）通过写操作控制，
            本协程读取该状态后据此决定是否模拟电流，实现简单的联动效果。
        """
        while True:
            # 在基准温度 25.0℃ 上叠加随机扰动，模拟真实传感器波动
            temp = 25.0 + random.uniform(-1, 1)
            self.device.setValues(3, 0, [int(temp * 10)])

            # 读取电机状态寄存器，判断当前是否处于运行状态
            motor_status = self.device.getValues(3, 2, count=1)[0]

            if motor_status == 1:
                # 电机运行：模拟 10.0~20.0A 随机电流（存储值为实际值 × 10）
                current = random.randint(100, 200)
                self.device.setValues(3, 4, [current])
                print(f"[虚拟PLC] 温度: {temp:.1f}°C | 电流: {current / 10:.1f}A | 电机运行")
            else:
                # 电机停止：电流清零，无需更新寄存器（初值已为 0）
                print(f"[虚拟PLC] 温度: {temp:.1f}°C | 电机停止")

            await asyncio.sleep(3)

    async def run(self):
        """
        启动虚拟 PLC 服务：创建数据更新协程并开启 Modbus TCP Server。

        说明：
            asyncio.create_task() 将数据模拟协程加入事件循环后台运行，
            StartAsyncTcpServer() 阻塞当前协程，持续监听并响应 Modbus 请求，
            两者在同一事件循环中并发执行，互不阻塞。
        """
        print("虚拟PLC启动，监听 0.0.0.0:5020，等待采集器连接...")

        # 在后台并发运行数据模拟协程
        asyncio.create_task(self.update_sensor_data())

        # 启动 Modbus TCP Server，阻塞直到进程退出
        await StartAsyncTcpServer(
            context=self.context,
            address=("0.0.0.0", 5020),
        )


if __name__ == "__main__":
    plc = VirtualPLC()
    asyncio.run(plc.run())
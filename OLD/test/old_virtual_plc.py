# virtual_plc.py
from pymodbus.server import StartTcpServer
from pymodbus.datastore import ModbusSlaveContext, ModbusServerContext
from pymodbus.datastore import ModbusSequentialDataBlock
import threading
import time
import random

class VirtualPLC:
    def __init__(self):
        """
        初始化虚拟PLC。
        创建100个保持寄存器（对应地址40001-40100），初始值全为0。
        """
        # 创建数据块：起始地址为0，初始化100个值为0的寄存器
        store = ModbusSlaveContext(
            hr=ModbusSequentialDataBlock(0, [0] * 100)  # hr: Holding Register
        )
        self.context = ModbusServerContext(slaves=store, single=True)
        
        # 【你的任务点1】设置寄存器初始值
        self._set_initial_values()
        
        # 一个标志位，用于模拟电机启动后的电流变化
        self.motor_running = False
        
    def _set_initial_values(self):
        """设置寄存器的初始值"""
        # 注意：在Modbus中，我们操作的是“偏移量”。
        # 40001对应偏移量0，40002对应偏移量1，以此类推。
        
        # 设置温度初始值 (地址40001 -> 偏移量0) 为 25.0度
        # 由于寄存器通常存储整数，我们将浮点数乘以10倍（250）来保持一位小数精度
        self.context[0].setValues(3, 0, [250])  # 功能码3写保持寄存器
        
        # 设置湿度初始值 (地址40002 -> 偏移量1) 为 60.0%
        self.context[0].setValues(3, 1, [600])  # 600 代表 60.0%
        
        # 设置电机状态初始值 (地址40003 -> 偏移量2) 为 0 (停止)
        self.context[0].setValues(3, 2, [0])
        
        # 设置电机速度初始值 (地址40004 -> 偏移量3) 为 50%
        self.context[0].setValues(3, 3, [50])
        
        print("[虚拟PLC] 寄存器初始值设置完成。")
        
    def update_sensor_data(self):
        """在一个独立线程中，周期性地更新传感器数据"""
        while True:
            # 1. 模拟温度波动（24.0 - 26.0摄氏度）
            new_temp = 25.0 + random.uniform(-1, 1)
            # 将浮点数转换为整数存储（乘以10）
            temp_to_write = int(new_temp * 10)
            self.context[0].setValues(3, 0, [temp_to_write])
            
            # 2. 如果电机正在运行，模拟一个随机的运行电流值（地址40005）
            if self.motor_running:
                simulated_current = random.randint(100, 200)  # 模拟电流值
                self.context[0].setValues(3, 4, [simulated_current]) # 偏移量4
                print(f"[虚拟PLC] 状态更新 | 温度: {new_temp:.1f}C | 模拟电机电流: {simulated_current}A")
            else:
                print(f"[虚拟PLC] 状态更新 | 温度: {new_temp:.1f}C")
                
            time.sleep(3)  # 每3秒更新一次
            
    def run(self):
        """启动虚拟PLC服务器"""
        # 启动传感器更新线程
        sensor_thread = threading.Thread(target=self.update_sensor_data, daemon=True)
        sensor_thread.start()
        
        # 启动Modbus TCP服务器，监听5020端口
        print("[虚拟PLC] 服务器启动在 localhost:5020")
        StartTcpServer(context=self.context, address=("0.0.0.0", 5020))
        
if __name__ == "__main__":
    plc = VirtualPLC()
    plc.run()
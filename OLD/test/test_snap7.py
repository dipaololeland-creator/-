import snap7
from snap7.util import get_bool
from snap7.type import Area  # ⚠️ 修正了这里的 type 和 Area (单数形式)

# 1. 实例化客户端
client = snap7.client.Client()

try:
    # 2. 连接到 PLC (注意：IP 填你电脑的物理 IP，即 NetToPLCsim 桥接的地址)
    print("🔄 正在连接 PLC (192.168.1.3)...")
    client.connect("192.168.1.3", 0, 1)
    print("✅ 成功连接到 PLC！")

    # 3. 读取 M 区 (Area.MK), 从第 10 个字节开始，读取 1 个字节长度
    # 这 1 个字节里包含了 M10.0 到 M10.7 的所有状态
    data = client.read_area(Area.MK, 0, 10, 1)

    # 4. 解析位状态
    # get_bool(字节数据, 字节偏移量, 位偏移量)
    m10_0 = get_bool(data, 0, 0)  # M10.0
    m10_1 = get_bool(data, 0, 1)  # M10.1
    m10_2 = get_bool(data, 0, 2)  # M10.2

    # 5. 打印结果
    print("-" * 30)
    print(f"🟢 启动按钮 (M10.0): {m10_0}")
    print(f"🔴 停止按钮 (M10.1): {m10_1}")
    print(f"⚙️ 电机线圈 (M10.2): {m10_2}")
    print("-" * 30)

except Exception as e:
    print(f"❌ 发生异常: {e}")

finally:
    # 6. 断开连接，释放资源
    client.disconnect()
    print("🔌 已断开与 PLC 的连接")
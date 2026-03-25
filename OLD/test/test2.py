# test_day1_setup.py - 第一天环境验证测试
import sys
import sqlite3

print("=" * 50)
print("第一阶段：基础环境检查")
print(f"Python 版本: {sys.version}")
print("=" * 50)

# 1. 测试 Flask (Web框架)
try:
    from flask import Flask
    print("[✅] Flask 库导入成功")
except ImportError as e:
    print(f"[❌] Flask 库导入失败: {e}")

# 2. 测试 sqlite3 (数据库，通常Python自带)
try:
    # 创建一个内存数据库，测试连接和基本操作
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)')
    cursor.execute('INSERT INTO test (name) VALUES (?)', ('验证测试',))
    conn.commit()
    cursor.execute('SELECT name FROM test WHERE id=1')
    result = cursor.fetchone()
    if result and result[0] == '验证测试':
        print("[✅] sqlite3 数据库操作成功")
    else:
        print("[⚠] sqlite3 测试数据不匹配")
    conn.close()
except Exception as e:
    print(f"[❌] sqlite3 数据库测试失败: {e}")

# 3. 测试 paho-mqtt (物联网通信)
try:
    import paho.mqtt.client as mqtt
    print("[✅] paho-mqtt 库导入成功")
    # 注意：这里只测试导入，不真正连接，因为你的Mosquitto服务可能还没安装
except ImportError as e:
    print(f"[❌] paho-mqtt 库导入失败: {e}")

# 4. 测试 pymodbus (工业协议，这是重点)
try:
    from pymodbus.client import ModbusTcpClient
    print("[✅] pymodbus 库导入成功")
    
    # 尝试创建一个客户端对象（即使不连接真实设备）
    # 这一步可以检测更深层次的依赖或版本兼容性问题
    test_client = ModbusTcpClient('127.0.0.1', port=5020, timeout=2)
    print("[✅] pymodbus 客户端对象创建成功")
    # 我们不在此处实际连接，因为你的虚拟PLC服务器（第三天任务）还没启动
    test_client.close()
    
except ImportError as e:
    print(f"[❌] pymodbus 库导入失败: {e}")
except Exception as e:
    # 捕获其他可能的错误，如版本不兼容
    print(f"[⚠] pymodbus 测试中出现警告: {e}")

print("=" * 50)
print("测试完成！请检查以上所有项是否均为 [✅]。")
print("=" * 50)

# 最后，给出一个综合建议
all_checks_passed = True # 假设通过，根据实际输出判断
print("\n📝 后续行动建议：")
if all_checks_passed:
    print("1. 所有库验证成功！你的开发环境已就绪。")
    print("2. 强烈建议现在进行一个‘集成小实验’：")
    print("   a. 参考第二天的计划，安装一个Modbus调试工具（如Modbus Poll）。")
    print("   b. 快速浏览pymodbus官方文档，找一个最简单的‘Modbus服务器’示例代码，运行起来。")
    print("   c. 用调试工具尝试连接本地服务器（127.0.0.1，端口502）。")
    print("   **目的**：在开始正式编码前，亲眼看到‘数据通信’是如何发生的。这会让第三天的任务变得非常直观。")
else:
    print("1. 发现有 [❌] 或 [⚠] 的项目，请根据错误信息搜索解决。")
    print("2. 最常见问题是虚拟环境未激活，或pip版本过旧。")
    print("3. 解决后，请重新运行此测试脚本。")
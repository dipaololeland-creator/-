# 测试所有库是否安装成功
try:
    import pymodbus
    print("✓ pymodbus 安装成功")
    print(f"  版本：{pymodbus.__version__}")
except ImportError as e:
    print("✗ pymodbus 安装失败")

try:
    import paho.mqtt.client as mqtt
    print("✓ paho-mqtt 安装成功")
except ImportError as e:
    print("✗ paho-mqtt 安装失败")

try:
    import flask
    print("✓ flask 安装成功")
    print(f"  版本：{flask.__version__}")
except ImportError as e:
    print("✗ flask 安装失败")

try:
    import sqlite3
    print("✓ sqlite3 可用")
    print(f"  SQLite版本：{sqlite3.sqlite_version}")
except ImportError as e:
    print("✗ sqlite3 不可用")

print("\n所有库测试完成！")
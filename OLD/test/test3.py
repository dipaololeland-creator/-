import paho.mqtt.client as mqtt

def on_message(client, userdata, msg):
    print(f"收到消息: {msg.topic} -> {msg.payload.decode()}")

client = mqtt.Client()
client.on_message = on_message
client.connect("broker.hivemq.com", 1883)
client.subscribe("warehouse/data")
client.loop_forever()  # 保持运行，等待消息
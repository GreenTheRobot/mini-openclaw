#!/usr/bin/env python3
"""每隔1分钟向微信文件传输助手发送'你好'，最多发送3次。"""
import os
import sys
import json
import time

COUNTER_FILE = ".send_hello_counter.json"

def get_count():
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE) as f:
            data = json.load(f)
            return data.get("count", 0)
    return 0

def save_count(count):
    with open(COUNTER_FILE, "w") as f:
        json.dump({"count": count}, f)

def main():
    count = get_count()
    if count >= 3:
        print(f"已达最大发送次数 ({count}/3)，不再发送。")
        return

    # 使用 wechat_file_transfer 发送消息
    # 这里通过调用 mini-openclaw 的桥接服务来发送
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c", f"""
from tools.wechat import send_text
send_text("你好", target="文件传输助手")
        """],
        capture_output=True, text=True
    )
    print(f"发送结果: {result.stdout}")
    if result.returncode != 0:
        print(f"发送失败: {result.stderr}")

    count += 1
    save_count(count)
    print(f"已发送 {count}/3 次")

if __name__ == "__main__":
    main()

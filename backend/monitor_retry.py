"""监控任务 c64fc4f53f70 的重试进度"""
import urllib.request
import json
import time
import sys

TASK_ID = "c64fc4f53f70"
URL = f"http://127.0.0.1:8090/api/replica/review/{TASK_ID}"
DURATION_SEC = 600  # 监控 10 分钟
INTERVAL_SEC = 15

last_log_count = 0
start = time.time()

while time.time() - start < DURATION_SEC:
    try:
        req = urllib.request.Request(URL)
        with urllib.request.urlopen(req, timeout=5) as r:
            d = json.loads(r.read())
        status = d.get("status")
        phase = d.get("phase")
        step_name = d.get("step_name")
        logs = d.get("logs") or []

        # 打印新增日志
        if len(logs) > last_log_count:
            for line in logs[last_log_count:]:
                print(f"  {line}")
            last_log_count = len(logs)

        elapsed = int(time.time() - start)
        print(f"[{elapsed}s] status={status} phase={phase} step={step_name} logs={len(logs)}")

        if status in ("failed", "awaiting_review"):
            print(f"\n=== 任务结束: {status} ===")
            if d.get("error"):
                print(f"错误: {d['error'][:300]}")
            break

    except Exception as e:
        print(f"[监控错误] {e}")

    time.sleep(INTERVAL_SEC)

print("\n监控结束")

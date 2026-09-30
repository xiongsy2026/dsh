"""段1 v2 重跑（开盖+取糖动作），下载失败则查 task 状态补下载。"""
import os
import sys
import time
import uuid

sys.path.insert(0, r"D:\video-skill")
os.chdir(r"D:\video-skill")

from backend.services.omni_generate import run_omni
from backend.services.omni_client import fetch_omni_task, download_video, OmniError

BASE = r"C:\dsh\productions\2026-09-26-铁软糖"
OUT = os.path.join(BASE, "outputs")
os.makedirs(OUT, exist_ok=True)

SEG1_V2 = (
    "In a single continuous 10-second shot, vertical 9:16, bright clean e-commerce style, "
    "high-key soft daylight, minimal shadows. A dark amber glass bottle of gummy candies with "
    "a bright red screw cap and a red label stands on a clean white surface. The camera slowly "
    "dollies in toward the bottle. A hand enters the frame, unscrews the red cap, lifts it off "
    "the bottle, then reaches into the bottle and takes out one glossy, wet-looking, vivid red "
    "gummy candy shaped like a small teddy bear. The hand lifts the candy toward the camera and "
    "holds it in the final seconds. No other bottles, no glass jars, no extra objects on the "
    "surface. No text overlay, no logo burn-in, no subtitle."
)


def try_download(video_url, name):
    ts = time.strftime("%Y%m%d-%H%M%S")
    save_path = os.path.join(OUT, f"{name}-v2-{ts}-{uuid.uuid4().hex[:6]}.mp4")
    try:
        download_video(video_url, save_path)
        print(f"[{name}] downloaded: {save_path}", flush=True)
        return save_path
    except OmniError as e:
        print(f"[{name}] download failed: {e}", flush=True)
        return None


def main():
    res = run_omni(prompt=SEG1_V2, model="omni_flash-10s", duration=10,
                   aspect_ratio="9:16", output_dir=OUT)
    print(f"[seg1-v2] success={res['success']} status={res['status']} "
          f"path={res['video_path']} task={res['task_id']} err={res['error']}", flush=True)

    path = res["video_path"]
    if not path and res.get("task_id"):
        # 提交成功但等待/下载断网：再查 task 补下载（不重复付费）
        for _ in range(5):
            time.sleep(15)
            info = fetch_omni_task(res["task_id"])
            print(f"[seg1-v2] task {res['task_id']} now: status={info['status']} "
                  f"progress={info['progress']} video_url={bool(info['video_url'])}", flush=True)
            if info["status"] == "completed" and info["video_url"]:
                path = try_download(info["video_url"], "seg1")
                break
            if info["status"] == "failed":
                break

    with open(os.path.join(BASE, "OMNI_RESULTS.md"), "w", encoding="utf-8") as f:
        f.write("# OMNI 结果（seg1 v2 重跑）\n\n")
        f.write(f"- seg1-v2: {path or '未成功'}\n")
        f.write("- seg2: 保留 omni-20260929-143015-ecdd2d.mp4\n")
    print(f"FINAL seg1-v2 path: {path}", flush=True)


if __name__ == "__main__":
    main()

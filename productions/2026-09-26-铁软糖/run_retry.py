"""重试验证：先查段1已提交 task 的状态（可能已生成完，避免重复付费），
再对失败段各重试一次（网络重试，不改 prompt）。"""
import os
import sys
import time
import uuid

sys.path.insert(0, r"D:\video-skill")
os.chdir(r"D:\video-skill")

from backend.services.omni_client import (
    fetch_omni_task,
    wait_for_completion,
    download_video,
    submit_omni_task,
    OmniError,
)
from backend.services.omni_generate import run_omni

BASE = r"C:\dsh\productions\2026-09-26-铁软糖"
OUT = os.path.join(BASE, "outputs")
os.makedirs(OUT, exist_ok=True)

SEG1_TASK = "task_A87FwCsZEM6TWMO4InunygrGmCxwWAld"

SEG1 = (
    "In a single continuous 10-second shot, vertical 9:16, bright clean e-commerce style, "
    "high-key soft daylight, minimal shadows. A dark amber glass bottle of gummy candies with "
    "a bright red screw cap and a red label stands on a clean white surface. The camera slowly "
    "dollies in toward the bottle, then gently tilts down as a hand unscrews the red cap and takes "
    "out one glossy, wet-looking, vivid red gummy candy shaped like a small teddy bear. The candy "
    "is held between two fingers and lifted toward the camera in the final seconds. No text overlay, "
    "no logo burn-in, no subtitle."
)

SEG2 = (
    "In a single continuous 10-second shot, vertical 9:16, bright high-key e-commerce style, "
    "soft daylight, clean white background, minimal shadows. Extreme close-up of a glossy vivid red "
    "translucent gummy candy with a teddy-bear shape, slowly rotating in front of the camera, surface "
    "catching light with a wet shine. The camera then slowly pulls back to reveal a small white "
    "sealed candy bag with a jagged edge lying next to the candy, both in sharp focus, clean minimal "
    "composition. No text overlay, no logo burn-in, no subtitle."
)


def try_download(video_url, name):
    ts = time.strftime("%Y%m%d-%H%M%S")
    save_path = os.path.join(OUT, f"{name}-{ts}-{uuid.uuid4().hex[:6]}.mp4")
    try:
        download_video(video_url, save_path)
        print(f"[{name}] downloaded: {save_path}", flush=True)
        return save_path
    except OmniError as e:
        print(f"[{name}] download failed: {e}", flush=True)
        return None


def main():
    paths = {"seg1": None, "seg2": None}

    # ── 段1：先查已提交 task ──
    print("== seg1: querying previous task ==", flush=True)
    for _ in range(3):
        info = fetch_omni_task(SEG1_TASK)
        print(f"[seg1] status={info['status']} progress={info['progress']} "
              f"video_url={bool(info['video_url'])} err={info['error']}", flush=True)
        if info["status"] == "completed":
            p = try_download(info["video_url"], "seg1")
            if p:
                paths["seg1"] = p
            break
        if info["status"] == "failed":
            break
        time.sleep(10)

    if not paths["seg1"]:
        print("[seg1] previous task unusable -> retry submit (network retry, same prompt)", flush=True)
        res = run_omni(prompt=SEG1, model="omni_flash-10s", duration=10,
                       aspect_ratio="9:16", output_dir=OUT)
        print(f"[seg1-retry] success={res['success']} status={res['status']} "
              f"path={res['video_path']} err={res['error']}", flush=True)
        if res["success"]:
            paths["seg1"] = res["video_path"]
        else:
            # 可能 task 提交成功但等待/下载断网：再查一次 task 状态
            if res.get("task_id"):
                time.sleep(5)
                info = fetch_omni_task(res["task_id"])
                print(f"[seg1-retry] task {res['task_id']} now: status={info['status']} "
                      f"video_url={bool(info['video_url'])}", flush=True)
                if info["status"] == "completed" and info["video_url"]:
                    p = try_download(info["video_url"], "seg1")
                    if p:
                        paths["seg1"] = p

    # ── 段2：重新提交（上次连 submit 都断了）──
    print("== seg2: retry submit ==", flush=True)
    res = run_omni(prompt=SEG2, model="omni_flash-10s", duration=10,
                   aspect_ratio="9:16", output_dir=OUT)
    print(f"[seg2-retry] success={res['success']} status={res['status']} "
          f"path={res['video_path']} err={res['error']}", flush=True)
    if res["success"]:
        paths["seg2"] = res["video_path"]
    else:
        if res.get("task_id"):
            time.sleep(5)
            info = fetch_omni_task(res["task_id"])
            print(f"[seg2-retry] task {res['task_id']} now: status={info['status']} "
                  f"video_url={bool(info['video_url'])}", flush=True)
            if info["status"] == "completed" and info["video_url"]:
                p = try_download(info["video_url"], "seg2")
                if p:
                    paths["seg2"] = p

    # ── 结果落盘 ──
    with open(os.path.join(BASE, "OMNI_RESULTS.md"), "w", encoding="utf-8") as f:
        f.write("# OMNI 结果（重试）\n\n")
        for k, v in paths.items():
            f.write(f"- {k}: {v or '未成功'}\n")
        f.write(f"\n全部成功: {all(paths.values())}\n")
    print(f"FINAL: {paths}", flush=True)


if __name__ == "__main__":
    main()

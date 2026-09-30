"""批量跑 omni 两段（9:16 / 10s），付费调用。"""
import os
import sys

sys.path.insert(0, r"D:\video-skill")
os.chdir(r"D:\video-skill")

from backend.services.omni_generate import run_omni

BASE = r"C:\dsh\productions\2026-09-26-铁软糖"
OUT = os.path.join(BASE, "outputs")

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


def main():
    os.makedirs(OUT, exist_ok=True)
    paths = []
    for name, prompt in (("seg1", SEG1), ("seg2", SEG2)):
        res = run_omni(prompt=prompt, model="omni_flash-10s", duration=10,
                       aspect_ratio="9:16", output_dir=OUT)
        print(f"[{name}] success={res['success']} status={res['status']} "
              f"path={res['video_path']} task={res['task_id']} err={res['error']}",
              flush=True)
        if res["success"]:
            paths.append(res["video_path"])
        else:
            print(f"[{name}] FAILED: {res['error']}", flush=True)

    with open(os.path.join(BASE, "OMNI_RESULTS.md"), "w", encoding="utf-8") as f:
        f.write("# OMNI 结果\n\n")
        for i, p in enumerate(paths, 1):
            f.write(f"- 段{i}: {p}\n")
        if len(paths) == 2:
            f.write(f"\n全部成功，可 ffmpeg concat。\n")
    print("DONE")


if __name__ == "__main__":
    main()

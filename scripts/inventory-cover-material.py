import glob
import os
import time

print("== productions/*/outputs 里的成片 ==")
for p in sorted(glob.glob("productions/*/outputs/*.mp4")):
    print("  %-64s %5.1f MB  %s" % (p.replace("\\", "/"), os.path.getsize(p) / 1048576,
                                time.strftime("%m-%d %H:%M", time.localtime(os.path.getmtime(p)))))

print("== video_runs 里有图的目录（前 10）==")
for d in sorted(glob.glob("video_runs/*/")):
    n = len(glob.glob(d + "**/*.jpg") + glob.glob(d + "**/*.png"))
    if n:
        print("  %-46s %d 张" % (d.replace("\\", "/"), n))

print("== hypit_runs 里有 mp4 的工程 ==")
for d in sorted(glob.glob("hypit_runs/*/")):
    v = glob.glob(d + "**/*.mp4")
    if v:
        newest = max(v, key=os.path.getmtime)
        print("  %-40s %d 个 mp4  最新 %s (%.1f MB)" % (d.replace("\\", "/"), len(v),
                                                      os.path.basename(newest), os.path.getsize(newest) / 1048576))

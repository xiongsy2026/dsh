import os

d = r"C:\dsh\productions\2026-09-26-铁软糖\outputs"
seg1 = os.path.join(d, "omni-20260929-144939-01a856.mp4")
seg2 = os.path.join(d, "omni-20260929-143015-ecdd2d.mp4")
with open(os.path.join(d, "concat-v2.txt"), "w", encoding="utf-8") as f:
    f.write("file '" + seg1.replace("\\", "/") + "'\n")
    f.write("file '" + seg2.replace("\\", "/") + "'\n")
print(open(os.path.join(d, "concat-v2.txt"), encoding="utf-8").read())

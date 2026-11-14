import hashlib
import os
import time
WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROOTS = [
    os.path.join(WS, r"plugins\super-clone-ui\lib\client.js"),
    r"C:\Users\Administrator\AppData\Local\Programs\DSH Desktop\resources\app\node_modules\@weeksir\super-clone-ui\lib\client.js",
]
# 再全盘扫一遍有没有第三份同名文件（同名两代安装是最容易误判的源）
for base in (r"C:\Users\Administrator\AppData\Local\Programs\DSH Desktop\resources\app",
             WS):
    for dirpath, dirnames, filenames in os.walk(base):
        if "super-clone-ui" not in dirpath:
            continue
        if "client.js" in filenames:
            p = os.path.join(dirpath, "client.js")
            if p not in ROOTS:
                ROOTS.append(p)

for p in ROOTS:
    if not os.path.isfile(p):
        print("不存在 :", p)
        continue
    b = open(p, "rb").read()
    link = os.path.islink(p)
    print("%-8s %-8s %s" % ("LINK" if link else "file",
                            time.strftime("%m-%d %H:%M:%S", time.localtime(os.path.getmtime(p))),
                            p))
    print("         bytes=%d sha=%s rev=%s 新宽度算式=%s"
          % (len(b), hashlib.sha256(b).hexdigest()[:8],
             (b"dock-20261004" + b"p") in b or b"20261004p" in b,
             b"width:calc(228px * 4 + 12px * 3)" in b))

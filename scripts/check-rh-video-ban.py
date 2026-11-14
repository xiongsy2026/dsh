#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闸：RunningHub 视频禁令（2026-10-09 用户指令"视频严格杜绝走这条线路"）。

钉的是【规则】不是现场：不起进程、不碰网络、不花一分钱 —— 传输层整个换成计数器，
所以"禁令没拦住"在这里的唯一表现就是【真打出去一次 POST】。每条断言都同时看异常和 HTTP 次数。

R1  默认（不传 spend_kind）⇒ 拒 + 0 HTTP
R2  显式 "video"          ⇒ 拒 + 0 HTTP
R3  显式豁免 edit/image    ⇒ 真放行到 /openapi/v2/run/…（正向对照，防"永远拒"式假安全）
R4  白名单不是黑名单       ⇒ ""/None/"banana"/"VIDEO" 一律拒（旧写法 `== "video"` 传别的就绕过）
R5  只拦【提交】           ⇒ 上传/查询/轮询不受影响（断之前已提交的任务要能用 taskId 捞回）
R6  拒绝文案不许自带绕过说明书
R7  豁免清单是唯一源，值必须是 ("edit","image")
R8  全仓库声明豁免的提交点 == 恰好 {gateway:edit, detail_image_agent:image}
R9  全仓库吃默认被禁的提交点 == 恰好已知那 6 处（多一处=新腿进来了，少一处=有人偷偷豁免）
R10 自带 RH 提交端点的服务文件必须逐个登记（咽喉 + 两条加工腿 + 一条上守卫的 H3 腿）
R12 自己拼 HTTP、又真会生成视频的腿 ⇒ 必须在提交【之前】调同一份 rh_video_guard（判据只有一份）
R13 那两条腿【真调一次】也要拒且 0 请求 —— 源码顺序只是弱证据，运行时才是判据
R11 网关侧 provider=runninghub 的 video.generate 只剩 h3/motion，且这两条都没声明豁免 ⇒ 必然吃 R1
"""
import asyncio
import io
import os
import re
import sys
import tempfile

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, WS)
sys.path.insert(0, BACKEND)

SCRATCH = tempfile.mkdtemp(prefix="rh-ban-")
os.environ["GATEWAY_SPEND_APPROVALS"] = os.path.join(SCRATCH, "approvals.json")
os.environ["GATEWAY_LEDGER"] = os.path.join(SCRATCH, "ledger.sqlite")

PASS, FAIL = [], []


def _safe(s):
    """按【输出流自己的编码】做 errors=replace。绝不 reconfigure 成 utf-8（那会造"零输出 + exit 0"的静默假绿）。
    2026-10-10 实测：这条 print 撞上 cp936 的 ⇒ 直接抛 UnicodeEncodeError ⇒ 闸【崩】而不是【红】，
    退出码也是 1，但没有一条判据跑完 ⇒ 谁都不知道剩下那几十条到底过没过。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    ok = got == want
    (PASS if ok else FAIL).append(name)
    print(_safe("%-4s %-56s got=%r want=%r" % ("PASS" if ok else "FAIL", name, got, want)))


def strip_py(s: str) -> str:
    """剥掉 # 注释和三引号文档串 —— 不然一条写在 docstring 里的 URL 会把闸判红（这次真踩到了）。"""
    s = re.sub(r'"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\'', "", s)
    return re.sub(r"(?m)#.*$", "", s)


from backend import gateway as gw                                           # noqa: E402
from backend.services import runninghub_client as rh          # noqa: E402

# ---- 只换传输层：函数体、判据、URL 拼装全是真身 ----
REQ = []


class FakeResp:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"code": 0, "msg": "success", "taskId": "FAKE-RH-T1", "status": "QUEUED",
                "data": {"fileName": "openapi/fake.png", "download_url": "https://rh.invalid/fake.png"}}


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        REQ.append(("POST", url))
        return FakeResp()

    async def get(self, url, **kw):
        REQ.append(("GET", url))
        return FakeResp()


class FakeHttpx:
    AsyncClient = FakeClient

    @staticmethod
    def Timeout(*a, **k):
        return None

    class HTTPStatusError(Exception):
        pass


rh.httpx = FakeHttpx
CLI = rh.RunningHubClient(api_key="FAKE-KEY")          # 不碰真 key


def submit(**kw):
    REQ.clear()
    try:
        res = asyncio.run(CLI.submit_task(workflow_id="WF-TEST", node_info_list=[], **kw))
        return None, list(REQ), res
    except Exception as e:                                  # noqa: BLE001
        # 🔴 别截太短：RH_VIDEO_BANNED 有 163 字，截到 160 会把"拒因是不是禁令"这条判据切成假红
        return "%s: %s" % (type(e).__name__, str(e)[:400]), list(REQ), None


BANNED = rh.RH_VIDEO_BANNED

# ---- R1/R2 默认与显式 video 都拒，且一次 HTTP 都没发 ----
for tag, kw in (("R1 默认(不声明)", {}), ("R2 显式 video", {"spend_kind": "video"})):
    err, calls, _ = submit(**kw)
    check("%s ⇒ 抛 RuntimeError" % tag, (err or "").startswith("RuntimeError"), True)
    check("%s ⇒ 0 HTTP（不是发出去再报错）" % tag, len(calls), 0)
    check("%s ⇒ 拒因就是禁令" % tag, BANNED in (err or ""), True)

# ---- R3 正向对照：显式豁免真的放行到提交 ----
for kind, aiapp in (("edit", True), ("image", False)):
    err, calls, res = submit(spend_kind=kind, is_ai_app=aiapp)
    urls = [u for m, u in calls]
    check("R3 %s ⇒ 放行、不抛" % kind, err, None)
    check("R3 %s ⇒ 恰好 1 次提交" % kind, len(urls), 1)
    check("R3 %s ⇒ 打的是 RH 的 run 端点" % kind,
          bool(urls) and "/openapi/v2/run/" in urls[0], True)
    check("R3 %s ⇒ 真拿到 taskId（不是空转）" % kind, (res or {}).get("taskId"), "FAKE-RH-T1")

# ---- R4 白名单，不是黑名单 ----
for bad in ("", None, "banana", "VIDEO", "video ", "generate", "edit2"):
    err, calls, _ = submit(spend_kind=bad)
    check("R4 spend_kind=%r ⇒ 拒且 0 HTTP" % bad,
          (BANNED in (err or "")) and not calls, True)

# ---- R5 只拦提交：上传/查询不受影响 ----
tmpf = os.path.join(SCRATCH, "probe.png")
io.open(tmpf, "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
REQ.clear()
try:
    got = asyncio.run(CLI.upload_resource(tmpf))
    up_err = None
except Exception as e:                                          # noqa: BLE001
    got, up_err = None, str(e)[:120]
check("R5a 上传没被禁令波及（豁免腿要能备料）", up_err, None)
check("R5b 上传打的是 media/upload/binary",
      any("/openapi/v2/media/upload/binary" in u for m, u in REQ), True)
check("R5c 返回 fileName 而不是 download_url（媒体节点必需）", got, "openapi/fake.png")
SRC = io.open(os.path.join(BACKEND, r"services\runninghub_client.py"), encoding="utf-8").read()
for fn in ("query_task", "upload_resource"):
    seg = SRC[SRC.index("def " + fn + "("):]
    seg = seg[:seg.index("\n    async def ") if "\n    async def " in seg else len(seg)]
    check("R5 %s 里没有禁令引用" % fn, "RH_VIDEO_BANNED" in seg, False)
check("R5 模块级轮询函数里也没有", "RH_VIDEO_BANNED" in SRC[SRC.index("async def poll_task_until_done"):], False)

# ---- R6 拒绝文案是攻击面 ----
check("R6a 文案里不许出现豁免参数名", "spend_kind" in BANNED, False)
check("R6b 文案里不许有可照抄的命令",
      bool(re.search(r"(python|gateway\.py|curl|powershell|RH_VIDEO_EXEMPT)", BANNED, re.I)), False)
check("R6c 文案仍要点名替代路（被拦的人要知道往哪走）",
      all(k in BANNED for k in ("seedance", "wan3", "motion-rpa")), True)

# ---- R7 豁免清单唯一源 ----
check("R7 豁免清单 == ('edit','image')（加一条要过这道闸）", list(rh.RH_VIDEO_EXEMPT), ["edit", "image"])

# ---- R8/R9 泛化：枚举全仓库经该客户端的提交点 ----
ROOT = BACKEND
SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "outputs", "tasks", ".dbg"}
L = SRC.split("\n")
EXEMPT_SITES, DEFAULT_SITES = {}, {}
for dp, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP]
    for fn in files:
        if not fn.endswith(".py"):
            continue
        p = os.path.join(dp, fn)
        try:
            s = strip_py(io.open(p, encoding="utf-8", errors="replace").read())
        except Exception:                                                   # noqa: BLE001
            continue
        if ".submit_task(" not in s:
            continue
        rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
        if rel != "backend/services/runninghub_client.py" and "runninghub_client" not in s:
            continue                                                        # 别人家的同名函数不算
        lines = s.split("\n")
        for i, l in enumerate(lines):
            if ".submit_task(" not in l or l.strip().startswith("#"):
                continue
            blk = "\n".join(lines[i:i + 9])
            m = re.search(r'spend_kind\s*=\s*["\'](\w*)["\']', blk)
            if m:
                EXEMPT_SITES.setdefault(rel, []).append(m.group(1))
            else:
                DEFAULT_SITES[rel] = DEFAULT_SITES.get(rel, 0) + 1

check("R8 声明豁免的提交点恰好是 gateway:edit + detail_image:image",
      {k: sorted(v) for k, v in sorted(EXEMPT_SITES.items())},
      {"backend/gateway.py": ["edit"], "backend/services/detail_image_agent.py": ["image"]})
check("R8b 声明的豁免值全在唯一源里（不许自造第三种）",
      sorted({k for v in EXEMPT_SITES.values() for k in v} - set(rh.RH_VIDEO_EXEMPT)), [])
check("R9 吃默认值被禁的提交点恰好是已知 6 处",
      {k: v for k, v in sorted(DEFAULT_SITES.items())},
      {"backend/services/h3_director_client.py": 1,
       "backend/services/product_motion/pipeline.py": 1,
       "backend/services/replica_wan3.py": 1,
       "backend/services/runninghub_client.py": 3})

# ---- R10 咽喉唯一性：服务代码里不许有第二条【生成视频】的提交路径 ----
SUBMIT_EP = re.compile(r"openapi/v2/run|openapi/v2/minimax|/task/openapi/ai-app/run|/task/openapi/workflow/run")
# 已登记的第二条腿：自己拼 HTTP、但【不是生成视频】。加一条要在这里写明它挂哪个能力，R10b 会验它没挂在 video.generate 上。
SIDE_LEG_FILES = {"backend/services/video_translate_pipeline.py": "lipsync",
                  "backend/services/voiceover_replace.py": "lipsync"}
# 自己拼 HTTP、【确实会生成视频】的腿 ⇒ 必须在提交之前调同一份守卫（R12 钉顺序）。
GUARDED_SELF_SUBMIT = {"backend/services/super_clone_wan3.py", "structural_replica_workflow.py"}
CORE_DIRS = ("backend", "pipeline")


def service_files():
    """服务代码 = app.py + backend/** + pipeline/** + 【被这三处 import 到的根级模块】。
    2026-10-09 补：app.py 里有一句 `from structural_replica_workflow import {phase_func}`（动态 import），
    只看目录会把一条真在跑的腿扫成"一次性脚本" ⇒ 那条腿有没有守卫就没人管了。"""
    out, seeds, names = set(), [], set()
    if os.path.isfile(os.path.join(ROOT, "app.py")):
        seeds.append(os.path.join(ROOT, "app.py"))
    for d in CORE_DIRS:
        for dp, dirs, files in os.walk(os.path.join(ROOT, d)):
            dirs[:] = [x for x in dirs if x not in SKIP]
            seeds += [os.path.join(dp, f) for f in files if f.endswith(".py")]
    for p in seeds:
        try:
            s = strip_py(io.open(p, encoding="utf-8", errors="replace").read())
        except Exception:                                                   # noqa: BLE001
            continue
        out.add(os.path.relpath(p, ROOT).replace(os.sep, "/"))
        names |= set(re.findall(r"from\s+([A-Za-z_]\w*)\s+import", s))
        names |= set(re.findall(r"^\s*import\s+([A-Za-z_]\w*)\s*$", s, re.M))
    for m in names:
        if os.path.isfile(os.path.join(ROOT, m + ".py")):
            out.add(m + ".py")
    return out


SVC_SET = service_files()
found_eps = []
for rel in sorted(SVC_SET):
    try:
        s = strip_py(io.open(os.path.join(ROOT, rel.replace("/", os.sep)), encoding="utf-8").read())
    except Exception:                                                       # noqa: BLE001
        continue
    if SUBMIT_EP.search(s):
        found_eps.append(rel)
check("R10 自带 RH 提交端点的服务文件 == 咽喉 + 两条加工腿 + 两条已上守卫的 H3 腿",
      sorted(set(found_eps)),
      sorted(["backend/services/runninghub_client.py"] + list(SIDE_LEG_FILES) + list(GUARDED_SELF_SUBMIT)))
# 🔴 R12：凡是【自己拼 HTTP 且真会生成视频】的腿，必须在提交之前调同一份守卫。
#     这条是 2026-10-09 补的：super_clone_wan3 的 H3 打的是 /openapi/v2/minimax/hailuo-h3/…，
#     不经 submit_task，早先"唯一咽喉"的说法对它不成立 ⇒ 判据要跟着腿走，不是跟着文件走。
GUARD_CALL = re.compile(r"rh_video_guard\s*\(|\.submit_task\s*\(")
for f in sorted(GUARDED_SELF_SUBMIT | {"backend/services/runninghub_client.py"}):
    try:
        s2 = strip_py(io.open(os.path.join(ROOT, f.replace("/", os.sep)), encoding="utf-8").read())
    except Exception as e:                                                  # noqa: BLE001
        check("R12 %s 读得到" % f, "read-fail:%s" % type(e).__name__, True)
        continue
    m_sub, m_guard = SUBMIT_EP.search(s2), GUARD_CALL.search(s2)
    check("R12 %s 有提交端点" % f.rsplit("/", 1)[-1], bool(m_sub), True)
    check("R12 %s 守卫在提交【之前】" % f.rsplit("/", 1)[-1],
          bool(m_guard) and m_sub is not None and 0 <= m_guard.start() < m_sub.start(), True)
    # 反向对照：守卫挪到提交之后必须判红（否则这条断言是空转）
    check("R12b %s 反向对照：守卫写在提交后面要能被抓到" % f.rsplit("/", 1)[-1],
          bool(m_guard) and m_guard.start() > m_sub.start(), False)
for f, cap in sorted(SIDE_LEG_FILES.items()):
    mods = [k[0] for k, v in gw.ROUTES.items() if f.rsplit("/", 1)[-1][:-3] in str(v.get("client", ""))]
    check("R10b %s 没挂在 video.generate 上（这是它不必走咽喉的唯一理由）" % f.rsplit("/", 1)[-1],
          "video.generate" in mods, False)
    check("R10c %s 确实挂在登记的那个能力上" % f.rsplit("/", 1)[-1], cap in mods, True)
# 强度说到为止：这两条腿和咽喉打的是【同一个】/openapi/v2/run/ai-app 端点。
# 禁令是按【能力】放行、不是按端点，所以它们的豁免只靠"挂在 lipsync 上"这一条事实；
# 谁把 lipsync 换成生成类工作流 = 绕过禁令，这道闸拦不住，只能靠人看 R10 的登记表。
print(_safe("NOTE 加工腿 %s 与咽喉同端点：豁免依据=挂载能力，不是 URL 形状" % ", ".join(sorted(SIDE_LEG_FILES))))
# 非服务目录（根下一次性脚本 / skills）里的第二条路径：只报不判红，别把闸变成噪音机
loose = []
for dp, dirs, files in os.walk(ROOT):
    dirs[:] = [d for d in dirs if d not in SKIP and d not in ("backend", "pipeline")]
    for fn in files:
        if not fn.endswith(".py") or fn == "runninghub_client.py":
            continue
        p = os.path.join(dp, fn)
        try:
            s = strip_py(io.open(p, encoding="utf-8", errors="replace").read())
        except Exception:                                                   # noqa: BLE001
            continue
        if SUBMIT_EP.search(s):
            rel = os.path.relpath(p, ROOT).replace(os.sep, "/")
            if rel not in SVC_SET:                      # 服务码里的已经由 R10/R12 判红，别在这里重复报
                loose.append(rel)
print(_safe("\nNOTE 服务代码之外还有 %d 个文件自带 RH 提交端点（不判红，但它们是禁令管不到的腿）:" % len(loose)))
for x in sorted(loose)[:8]:
    print(_safe("       " + str(x)))
if len(loose) > 8:
    print(_safe("       …另 %d 个" % (len(loose) - 8)))

# ---- R11 网关侧：剩下的 RH 视频 lane 必然吃 R1 ----
rh_video = sorted(k[1] for k in gw.ROUTES
                  if k[0] == "video.generate" and str(gw.ROUTES[k].get("provider")) == "runninghub")
check("R11a 网关里 provider=runninghub 的 video.generate 只剩 h3/motion", rh_video, ["h3", "motion"])
check("R11b 这两条的提交点都没声明豁免 ⇒ 必然被咽喉拦住",
      all(f in DEFAULT_SITES for f in ("backend/services/h3_director_client.py",
                                       "backend/services/runninghub_client.py")), True)
check("R11c 加工类 lane 仍在（禁令不许误伤去水印/超分）",
      sorted(k[1] for k in gw.ROUTES if k[0] == "video.edit"), ["dewatermark", "enhance"])

# ---- R13 真调一次：那两条"自己拼 HTTP"的腿运行时也要拒（源码顺序只是弱证据）----
class FakePost:
    def __init__(self):
        self.calls = []

    async def post(self, url, **kw):
        self.calls.append(url)
        raise AssertionError("守卫没拦住，真发出去了: %s" % url)


def refuse(fn, *a, **k):
    try:
        fn(*a, **k)
        return "没抛"
    except AssertionError:
        return "放行(发真请求了)"
    except RuntimeError as e:
        return "banned" if BANNED[:24] in str(e) else "other:%s" % str(e)[:60]
    except Exception as e:                                        # noqa: BLE001
        return "other:%s:%s" % (type(e).__name__, str(e)[:60])


fp = FakePost()
try:
    import structural_replica_workflow as srw                     # noqa: E402
    check("R13a 结构性复刻的 H3 腿真调 ⇒ 运行时也拒",
          refuse(asyncio.run, srw._h3_submit_task(fp, "p", ["https://x.invalid/a.png"], "15")), "banned")
    check("R13b 且一个请求都没发", len(fp.calls), 0)
except Exception as e:                                            # noqa: BLE001
    check("R13a 结构性复刻模块可导入", "import-fail:%s" % type(e).__name__, "banned")

out_dir = os.path.join(SCRATCH, "h3-guard")
try:
    from backend.services import super_clone_wan3 as scw          # noqa: E402
    check("R13c 超级复刻的 H3 腿真调 ⇒ 运行时也拒",
          refuse(asyncio.run, scw.step2_h3_execute("p", os.path.join(SCRATCH, "nope.mp4"), [], 5.0, out_dir)),
          "banned")
    check("R13d 被拒时没上传素材、没落任何产物", os.listdir(out_dir) if os.path.isdir(out_dir) else [], [])
except Exception as e:                                            # noqa: BLE001
    check("R13c 超级复刻模块可导入", "import-fail:%s" % type(e).__name__, "banned")

print(_safe("\n=== RunningHub 视频禁令闸：%d/%d 通过 ===" % (len(PASS), len(PASS) + len(FAIL))))
for f in FAIL:
    print(_safe("  FAIL: " + str(f)))
print(_safe("临时目录: " + str(SCRATCH)))
sys.exit(1 if FAIL else 0)

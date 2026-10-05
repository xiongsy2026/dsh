#!/usr/bin/env python
"""视频去水印腿（RunningHub AI App）接入闸。默认 0 网络 0 花费。

钉的是四件事：
  A 五张表都点到这条（漏一张 = "能 resolve 不能 call" 或面板里是死路由）
  B 参数/凭证解析对（密钥只验长度，绝不打印）
  C 花钱【之前】的本地拦截：缺文件 / 公网 URL / 缺 output_path / fps·instance 不在白名单 / 缺节点号
  D 假传输层跑真代码：upload→run/ai-app→query→落盘 的每一次 HTTP 都记账，
    核端点路径、Bearer 头、nodeInfoList 四个节点、"上传拿的是 fileName 不是 download_url"、
    失败不留假成功、给 job_id 时【只查不重提交】（重提交=双花）。
--live 才真跑一条（会真按 GPU 秒扣钱；跑前照规矩先报价）。
"""
import io
import json
import os
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\video-skill")
import httpx  # noqa: E402
from backend import gateway as g  # noqa: E402

SRC = open(r"D:\video-skill\backend\gateway.py", encoding="utf-8").read()
CFG = open(r"D:\video-skill\config.py", encoding="utf-8").read()
LIVE = "--live" in sys.argv
out = []


def check(name, got, want):
    ok = json.dumps(got, ensure_ascii=False, default=str) == json.dumps(want, ensure_ascii=False, default=str)
    out.append((ok, name))
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else " => %r (期望 %r)" % (got, want)))


# ---- A 五张表 ----
check("A1 CAPABILITIES 有 video.edit", "video.edit" in g.CAPABILITIES, True)
check("A2 ROUTES 注册了 dewatermark", ("video.edit", "dewatermark") in g.ROUTES, True)
check("A3 DEFAULT_PURPOSE 指到它", g.DEFAULT_PURPOSE.get("video.edit"), "dewatermark")
check("A4 PRICE_ROUTES 在册（占位价也要在册）", ("video.edit", "dewatermark") in g.PRICE_ROUTES, True)
check("A5 PRICE_BY_CAP 兜底也在册", "video.edit" in g.PRICE_BY_CAP, True)
check("A6 _ADAPTED 声明已接", g.is_adapted("video.edit", "dewatermark"), True)
check("A7 call() 里有分发分支", 'capability == "video.edit" and r["purpose"] == "dewatermark"' in SRC, True)

# ---- B 解析（model 位放的是 AI App 号，不是模型名）----
r = g.resolve("video.edit")
check("B1 provider", r["provider"], "runninghub")
check("B2 base_url 是国际站", r["base_url"], "https://www.runninghub.ai")
check("B3 AI App 号来自 config（19 位）", len(str(r["model"])), 19)
check("B4 密钥就绪（只验长度，不打印）", len(r["api_key"] or ""), 32)
check("B5 摘要里不含密钥明文", all(x not in json.dumps(g.safe_summary(r), ensure_ascii=False)
                                   for x in ("api_key", r["api_key"])), True)
check("B6 节点号只住 config（gateway.py 里不许出现字面量 142/143/138/128 兜底）",
      all(('or "%s"' % n) not in SRC for n in ("142", "143", "138", "128")), True)
check("B7 四个节点号 + App 号 + 默认提示词都在 config",
      all(k in CFG for k in ("RUNNINGHUB_DEWATERMARK_APP_ID", "RUNNINGHUB_DEWATERMARK_VIDEO_NODE",
                             "RUNNINGHUB_DEWATERMARK_DURATION_NODE", "RUNNINGHUB_DEWATERMARK_FPS_NODE",
                             "RUNNINGHUB_DEWATERMARK_PROMPT_NODE", "RUNNINGHUB_DEWATERMARK_PROMPT")), True)

# ---- C 花钱前的本地拦截 ----
REAL = r"D:\video-skill\backend\services\file.mp4"   # 盘上真有个 mp4，只用来走"文件存在"这条路
check("C1 缺 video_path ⇒ 本地拒", (g.call("video.edit")["success"], "video_path" in (g.call("video.edit")["error"] or "")),
      (False, True))
check("C2 公网 URL 当输入 ⇒ 拒（上传步骤会炸，别让上游替我们报错）",
      g.call("video.edit", video_path="https://x/a.mp4")["error"].startswith("去水印的 video_path 只收本地文件"), True)
check("C3 文件不存在 ⇒ 拒", g.call("video.edit", video_path="C:/nope/a.mp4")["success"], False)
check("C4 缺 output_path ⇒ 拒（链接 24 小时失效，不落盘=白花钱）",
      "output_path" in (g.call("video.edit", video_path=REAL)["error"] or ""), True)
check("C5 fps 只认 16/24", g._dewatermark_payload({"video_path": REAL, "output_path": "x", "fps": "30"})[1].startswith("fps 只认"), True)
check("C6 instance_type 只认三档", g._dewatermark_payload({"video_path": REAL, "output_path": "x", "instance_type": "mega"})[1].startswith("instance_type"), True)
v, e = g._dewatermark_payload({"video_path": REAL, "output_path": "C:/dsh/.tmp-dw/out.mp4"})
check("C7 正常参数能过（时长由 ffprobe 探出来，>0）", (e, isinstance(v.get("duration"), int) and v["duration"] > 0), (None, True))
check("C8 默认提示词是上游文档那条（清 Sora/Seedance 文字图标）", "Sora" in v["prompt"] and "Seedance" in v["prompt"], True)

# ---- D 计费口径：按 GPU 秒 ⇒ 时长分档 ----
check("D1 5 秒 = 1 档", g.meter("video.edit", "dewatermark", {"duration": 5})["credits_estimate"], 20)
check("D2 25 秒 = 3 档", g.meter("video.edit", "dewatermark", {"duration": 25})["credits_estimate"], 60)
check("D3 占位价在源码里被标成「没有实测价」（别让人当成校准过的数）", "没有实测价" in SRC, True)


# ---- E 假传输层：只换 httpx，被测代码整块真跑 ----
class FakeResp:
    def __init__(self, payload=None, content=b"", code=200):
        self._p, self.content, self.status_code = payload or {}, content, code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=self)

    def json(self):
        return self._p


MODE = {"query": "SUCCESS", "body": b"V" * 4096}
CALLS = []
QUERIES = [0]


class FakeClient:
    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None, files=None, **kw):
        CALLS.append({"m": "POST", "url": url, "headers": headers or {}, "json": json, "multipart": bool(files)})
        if url.endswith("/media/upload/binary"):
            return FakeResp({"code": 0, "data": {"fileName": "RH-UP-9f3.mp4", "download_url": "https://rh/dl.mp4"}})
        if "/openapi/v2/run/" in url:
            return FakeResp({"taskId": "TASK-777", "status": "RUNNING"})
        if url.endswith("/openapi/v2/query"):
            QUERIES[0] += 1
            if MODE["query"] == "SUCCESS":
                return FakeResp({"status": "SUCCESS", "results": [{"url": "https://rh/out.mp4", "outputType": "mp4"}],
                                 "usage": {"taskCostTime": "63", "consumeMoney": "0.42"}})
            return FakeResp({"status": "FAILED", "errorMessage": "CUDA out of memory", "failedReason": {}})
        return FakeResp({})

    async def get(self, url, **kw):
        CALLS.append({"m": "GET", "url": url})
        return FakeResp(content=MODE["body"])


httpx.AsyncClient = FakeClient   # 只换传输层：upload_resource/submit_task/query_task 的真代码照常跑

vals, err = g._dewatermark_payload({"video_path": REAL, "output_path": os.path.join(tempfile.gettempdir(), "dw-e1.mp4")})
check("E0 前置：正常参数过校验", err, None)
CALLS.clear()
res = g._dewatermark_run(vals, "APP-TEST", api_key="FAKE-KEY", poll_interval=0.01)
urls = [c["url"].replace("https://www.runninghub.ai", "") for c in CALLS]
check("E1 三次写 + 一次下载，端点顺序对", urls,
      ["/openapi/v2/media/upload/binary", "/openapi/v2/run/ai-app/APP-TEST", "/openapi/v2/query", "https://rh/out.mp4"])
check("E2 每次都带 Bearer（且用的是传进来的 key，不读真密钥）",
      all(c.get("headers", {}).get("Authorization") == "Bearer FAKE-KEY" for c in CALLS if c["m"] == "POST"), True)
sub = next((c for c in CALLS if "/run/ai-app/" in c["url"]), None)
body = (sub or {}).get("json") or {}
nodes = {n["nodeId"]: n for n in body.get("nodeInfoList") or []}
check("E3 走的是 AI App 端点（/run/ai-app/，不是 /run/workflow/）", "/run/ai-app/" in str(urls[1]), True)
check("E4 四个节点齐（142 视频 143 时长 138 帧率 128 提示词）", sorted(nodes.keys()), ["128", "138", "142", "143"])
check("E5 video 节点塞的是上传返回的 fileName，不是 download_url", nodes.get("142", {}).get("fieldValue"), "RH-UP-9f3.mp4")
check("E6 时长/帧率/提示词三档都按字符串塞进 value/text",
      [(nodes.get("143", {}).get("fieldName"), nodes.get("138", {}).get("fieldName"), nodes.get("128", {}).get("fieldName"))],
      [("value", "value", "text")])
check("E7 instanceType 透传（default=24G）", body.get("instanceType"), "default")
check("E8 成功：落盘真有文件且非空", res["success"] and os.path.getsize(vals["output_path"]) == 4096, True)
check("E9 回信封带 job_id 与本地路径（交付要指名道姓）", (res["job_id"] == "TASK-777", bool(res["path"])), (True, True))
check("E10 上游 usage 透传回来（校准占位价就靠这个数）", res["upstream_usage"]["task_cost_time"], "63")
os.remove(vals["output_path"])

# 失败不许留假成功
CALLS.clear()
MODE["query"] = "FAILED"
vals2, _ = g._dewatermark_payload({"video_path": REAL, "output_path": os.path.join(tempfile.gettempdir(), "dw-e2.mp4")})
res2 = g._dewatermark_run(vals2, "APP-TEST", api_key="FAKE-KEY", poll_interval=0.01)
check("E11 上游 FAILED ⇒ success:false 且点名 OOM", (res2["success"], "memory" in res2["error"]), (False, True))
check("E12 失败也回 job_id（可据此查上游，不重提交）", res2["job_id"], "TASK-777")
check("E13 失败绝不在盘上留一个「成功文件」", os.path.exists(vals2["output_path"]), False)

# 200 但只有 200 字节 = 不是视频，别当成功落盘
MODE["query"] = "SUCCESS"
MODE["body"] = b"E404-ish" * 20
vals3, _ = g._dewatermark_payload({"video_path": REAL, "output_path": os.path.join(tempfile.gettempdir(), "dw-e3.mp4")})
res3 = g._dewatermark_run(vals3, "APP-TEST", api_key="FAKE-KEY", poll_interval=0.01)
check("E14 下载体过小 ⇒ 判失败（不把错误页存成 mp4）", res3["success"], False)
check("E15 过小那次也没落盘", os.path.exists(vals3["output_path"]), False)
MODE["body"] = b"V" * 4096

# 给 job_id = 只捞结果，绝不重提交（双花）
CALLS.clear()
vals4, _ = g._dewatermark_payload({"video_path": REAL, "output_path": os.path.join(tempfile.gettempdir(), "dw-e4.mp4")})
res4 = g._dewatermark_run(vals4, "APP-TEST", api_key="FAKE-KEY", job_id="TASK-ORPHAN", poll_interval=0.01)
check("E16 有 job_id ⇒ 不上传不提交，只查 + 下载", [c["url"].split("/openapi/")[-1] if "/openapi/" in c["url"] else c["url"]
                                                    for c in CALLS], ["v2/query", "https://rh/out.mp4"])
check("E17 查的是那个孤儿任务本身", next(c for c in CALLS if "query" in c["url"])["json"]["taskId"], "TASK-ORPHAN")
check("E18 捞回也算成功（0 分二次花费）", res4["success"], True)
os.remove(vals4["output_path"])

# ---- F 查询抖动不许变成重提交 ----
check("F1 轮询里查询异常退避重试同一个 taskId（连续 6 次才判死）",
      ("fails += 1" in SRC, "if fails >= 6:" in SRC, "min(poll_interval, 3 * fails)" in SRC), (True, True, True))
check("F2 判死时只报 taskId、不重新提交", "别重提交" in SRC, True)
check("F3 超时那次也回 job_id（不静默丢任务）",
      ('"error": "去水印：轮询超时' in SRC, '"job_id": tid' in SRC), (True, True))

if LIVE:
    print("\n--live：这条会真按 GPU 秒扣钱，先按 AGENTS.md 报价并等确认，"
          "命令形如 gateway.py call video.edit --account <member> --json '{...}'")

bad = [n for ok, n in out if not ok]
print("\n=== 去水印腿闸：%d 条断言，失败 %d ===" % (len(out), len(bad)))
sys.exit(1 if bad else 0)

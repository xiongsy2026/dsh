import io
import json
import re
import time
import urllib.request

SET = r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\settings.yaml"
lines = io.open(SET, encoding="utf-8", errors="replace").read().splitlines()
i = next(n for n, l in enumerate(lines) if l.strip() == "glm:")
j = next((n for n in range(i + 1, len(lines)) if re.match(r"^  \S", lines[n])), len(lines))
blk = "\n".join(lines[i:j])
base = re.search(r"baseURL:\s*(\S+)", blk).group(1)
key = re.search(r"apiKeyEnv:\s*(.+)", blk).group(1).strip()


def call(model, msgs, max_tokens=700):
    payload = {"model": model, "messages": msgs, "stream": False, "max_tokens": max_tokens,
               "temperature": 0.7}
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            o = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return {"err": type(e).__name__ + " " + str(e)[:90], "ms": int((time.time() - t0) * 1000)}
    ch = o.get("choices", [{}])[0]
    txt = (ch.get("message") or {}).get("content") or ""
    return {"ms": int((time.time() - t0) * 1000), "finish": ch.get("finish_reason"),
            "len": len(txt), "text": txt}


CASES = {
    "A 口播文案(30s 竖屏)": [{"role": "system", "content": "你是电商短视频口播文案写手，只输出文案本身。"},
                    {"role": "user", "content": "产品：黄色毛绒短款开衫（女装）。写一段 30 秒竖屏广告口播，卖点：不掉毛、不起球、显白。要口语、别用极限词。"}],
    "B 严格 JSON": [{"role": "user", "content": '把这句话拆成分镜："女孩走进咖啡馆，坐下，翻开书"。只输出 JSON 数组，每项含 shot/duration/shot_size，不要任何解释文字。'}],
}

for model in ["GLM-5.3-thinking", "Kimi3"]:
    print("\n########", model)
    for name, msgs in CASES.items():
        r = call(model, msgs)
        if "err" in r:
            print(" ", name, "→ 失败", r["err"]); continue
        body = r["text"]
        leaked = bool(re.search(r"Linux|容器|macOS|C:\\|/mnt/|无法访问|作为AI|抱歉", body))
        parsed = None
        if name.startswith("B"):
            m = re.search(r"\[.*\]", body, re.S)
            try:
                parsed = json.loads(m.group(0)) if m else None
            except Exception:
                parsed = "解析失败"
        print("  %s → %dms finish=%s 字数=%d 带环境/推脱话术=%s" % (name, r["ms"], r["finish"], len(body), leaked))
        if parsed is not None:
            print("      JSON 解析 =", "OK %d 项" % len(parsed) if isinstance(parsed, list) else parsed)
        print("      开头 90 字:", body[:90].replace("\n", " "))
    c = [call(model, [{"role": "user", "content": "只回答一个字：好"}], 60) for _ in range(3)]
    print("  C 连续三次 →", " ".join(("%dms/%s" % (x.get("ms"), x.get("finish"))) if "ms" in x else "ERR" for x in c))

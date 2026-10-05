import io
import json
import re
import urllib.request

SET = r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\settings.yaml"
lines = io.open(SET, encoding="utf-8", errors="replace").read().splitlines()
i = next(n for n, l in enumerate(lines) if l.strip() == "glm:")
j = next((n for n in range(i + 1, len(lines)) if re.match(r"^  \S", lines[n])), len(lines))
blk = "\n".join(lines[i:j])
base = re.search(r"baseURL:\s*(\S+)", blk).group(1)
key = re.search(r"apiKeyEnv:\s*(.+)", blk).group(1).strip()
ids = re.findall(r"^\s*-\s*id:\s*(\S+)\s*$", blk, re.M)


def post(path, payload, timeout=60):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:160]
        except Exception:
            pass
        return "ERR", (type(e).__name__ + " " + str(e)[:120] + " " + body)


try:
    with urllib.request.urlopen(base + "/models", timeout=15) as r:
        models = [m.get("id") for m in json.loads(r.read().decode())]
except Exception as e:
    models = None
    print("/models 取不到:", type(e).__name__, str(e)[:80])
if models is not None:
    print("中转认得的模型 id 数 =", len(models))
    print("配置里那 4 个是否都被认得:", {m: (m in models) for m in ids})

TOOL = [{"type": "function", "function": {
    "name": "run_shell",
    "description": "Run a shell command on the local machine and return its stdout.",
    "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}}]

print()
print("%-20s %-8s %-14s %-11s %s" % ("model id", "http", "finish_reason", "有tool_calls", "内容/错误"))
for m in ids:
    st, res = post("/chat/completions", {
        "model": m,
        "messages": [{"role": "user", "content": "List the files in C:\\dsh. You MUST call the run_shell tool."}],
        "tools": TOOL,
        "max_tokens": 200,
    })
    if st != 200:
        print("%-20s %-8s %-14s %-11s %s" % (m, st, "-", "-", str(res)[:70]))
        continue
    ch = res["choices"][0]
    msg = ch.get("message", {})
    tc = msg.get("tool_calls")
    print("%-20s %-8s %-14s %-11s %s" % (m, st, str(ch.get("finish_reason")), bool(tc),
                                          (json.dumps(tc[0]["function"], ensure_ascii=False)[:60] if tc else (msg.get("content") or "")[:60])))

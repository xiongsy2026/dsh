import io
import json
import re
import urllib.request
import zlib

SET = r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\settings.yaml"
lines = io.open(SET, encoding="utf-8", errors="replace").read().splitlines()
i = next(n for n, l in enumerate(lines) if l.strip() == "glm:")
j = next((n for n in range(i + 1, len(lines)) if re.match(r"^  \S", lines[n])), len(lines))
blk = "\n".join(lines[i:j])
base = re.search(r"baseURL:\s*(\S+)", blk).group(1)
key = re.search(r"apiKeyEnv:\s*(.+)", blk).group(1).strip()

MAGIC = b"\x28\xb5\x2f\xfd"
p = r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\sessions\--C-dsh--\session-ce0a5d26-aa62-40e7-82d6-81a16ee0f8a6\session.v3.jsonl.zstd"
raw = open(p, "rb").read()
i2, frames = 0, []
while True:
    a = raw.find(MAGIC, i2)
    if a < 0:
        break
    b = raw.find(MAGIC, a + 4)
    end = len(raw) if b < 0 else b
    try:
        frames.append(zlib.zstd_decompress(raw[a:end]) if hasattr(zlib, "zstd_decompress") else __import__("zlib").decompress(raw[a:end]))
    except Exception:
        frames.append(b"")
    if b < 0:
        break
    i2 = b
txt = b"\n".join(frames).decode("utf-8", "replace")
if "tool_calls" not in txt and len(txt) < 1000:
    import subprocess
    txt = subprocess.run(["node", "-e", """
const fs=require('fs'),z=require('zlib');const M=Buffer.from([0x28,0xb5,0x2f,0xfd]);
const raw=fs.readFileSync(process.argv[1]);let i=0,o=[];for(;;){const a=raw.indexOf(M,i);if(a<0)break;let b=raw.indexOf(M,a+4);const e=b<0?raw.length:b;try{o.push(z.zstdDecompressSync(raw.subarray(a,e)));}catch(x){}if(b<0)break;i=b;}
process.stdout.write(Buffer.concat(o).toString('utf8'));
""", p], capture_output=True).stdout.decode("utf-8", "replace")

ev = [json.loads(l) for l in txt.splitlines() if l.strip()]
try:
    ev = [e for e in ev if isinstance(e, dict)]
except Exception:
    pass
hdr = next(e for e in ev if e.get("type") == "request/header")
tools = hdr["data"]["header"]["tools"]
sysmsg = next((e for e in ev if e.get("type") == "system/message"), None)
users = [e for e in ev if e.get("type") == "user/message"]

def text_of(msg):
    c = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return json.dumps(msg, ensure_ascii=False)[:400]

sys_text = text_of(sysmsg["data"]["message"]) if sysmsg else ""
def pick(e):
    d = e.get("data", e)
    for k in ("message", "text", "content"):
        if k in d:
            return d[k]
    return d
um = pick(users[1]) if len(users) > 1 else pick(users[0])
u2 = text_of(um) if isinstance(um, dict) else str(um)
print("system 长度 =", len(sys_text), "| 工具数 =", len(tools), "| 重放的 user 内容 =", u2[:40])

payload = {
    "model": "GLM-5.3-thinking",
    "messages": [{"role": "system", "content": sys_text}, {"role": "user", "content": u2}],
    "tools": tools,
    "max_tokens": 400,
}
req = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(),
                             headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
with urllib.request.urlopen(req, timeout=120) as r:
    o = json.loads(r.read().decode("utf-8", "replace"))
ch = o["choices"][0]
tc = ch.get("message", {}).get("tool_calls")
print("重放 DSH 原始请求 → finish=%s  tool_calls=%s" % (ch.get("finish_reason"), bool(tc)))
if tc:
    print("   原始 tool_calls =", json.dumps(tc, ensure_ascii=False)[:400])
    names = {x["name"] for x in tools}
    for x in tc[:3]:
        nm = x["function"]["name"]
        print("   调用:", repr(nm), "在工具表里吗 =", nm in names, "| args:", str(x["function"].get("arguments"))[:60])
else:
    print("   纯文本:", (ch["message"].get("content") or "")[:120])

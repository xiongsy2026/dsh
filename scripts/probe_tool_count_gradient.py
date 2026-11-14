import io
import json
import re
import subprocess
import time
import urllib.request

SET = r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\settings.yaml"
lines = io.open(SET, encoding="utf-8", errors="replace").read().splitlines()
i = next(n for n, l in enumerate(lines) if l.strip() == "glm:")
j = next((n for n in range(i + 1, len(lines)) if re.match(r"^  \S", lines[n])), len(lines))
blk = "\n".join(lines[i:j])
base = re.search(r"baseURL:\s*(\S+)", blk).group(1)
key = re.search(r"apiKeyEnv:\s*(.+)", blk).group(1).strip()

# 取 DSH 真实那次请求声明的 39 个工具（从会话账本的 request/header 里读）
node = r"""
const fs=require('fs'),z=require('zlib');const M=Buffer.from([0x28,0xb5,0x2f,0xfd]);
const p=process.argv[1];const raw=fs.readFileSync(p);let i=0,o=[];
for(;;){const a=raw.indexOf(M,i);if(a<0)break;let b=raw.indexOf(M,a+4);const e=b<0?raw.length:b;
try{o.push(z.zstdDecompressSync(raw.subarray(a,e)));}catch(x){}if(b<0)break;i=b;}
const ev=Buffer.concat(o).toString('utf8').split('\n').filter(Boolean).map(l=>{try{return JSON.parse(l);}catch(e){return null;}}).filter(Boolean);
const h=(ev.find(e=>e.type==='request/header')||{data:{header:{tools:[]}}}).data.header.tools;
process.stdout.write(JSON.stringify(h));
"""
tp = r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\sessions\--C-dsh--\session-ce0a5d26-aa62-40e7-82d6-81a16ee0f8a6\session.v3.jsonl.zstd"
real = json.loads(subprocess.run(["node", "-e", node, tp], capture_output=True).stdout.decode("utf-8"))
print("DSH 真实工具数 =", len(real), "| 前 6 个 =", [t["name"] for t in real[:6]])

FAKE = [{"type": "function", "function": {
    "name": "run_shell", "description": "Run a shell command on the user's local Windows machine and return stdout.",
    "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}}]

ASK = "列出 C:\\dsh 目录下的文件。你必须调用工具来做，不要凭空回答。"


def wrap(tools):
    # 账本里 parameters 是字符串（闸打印时被截过），原样透传、不猜它的类型
    return [{"type": "function", "function": t} for t in tools]


def ask(model, tools):
    payload = {"model": model, "messages": [{"role": "user", "content": ASK}], "max_tokens": 300}
    if tools:
        payload["tools"] = tools
    req = urllib.request.Request(base + "/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            o = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return ("ERR", type(e).__name__, "", "")
    ch = o["choices"][0]
    msg = ch.get("message", {})
    tc = msg.get("tool_calls") or []
    return (str(ch.get("finish_reason")), ",".join(t["function"]["name"] for t in tc)[:40],
            (msg.get("content") or "")[:40].replace("\n", " "), str(msg.get("reasoning_content") or "")[:30].replace("\n", " "))


print()
print("%-20s %-8s %-14s %-42s %s" % ("model", "工具数", "finish_reason", "回上来的 tool_calls", "正文/思考"))
for model in ["GLM-5.3-thinking", "Kimi3"]:
    for label, tools in [("0", []), ("1", FAKE), ("5", wrap(real[:5])), ("39", wrap(real))]:
        fr, names, content, think = ask(model, tools)
        print("%-20s %-8s %-14s %-42s %s" % (model, label, fr, names or "（无）", (content or ("思考:" + think))[:44]))
        time.sleep(0.6)

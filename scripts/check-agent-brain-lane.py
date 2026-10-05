#!/usr/bin/env python
r"""DSH agent 脑的准入判据（2026-10-05 AFS/chat2api 事故的复发闸）。

跑法：python scripts/check-agent-brain-lane.py

背景（一句话）：本机 127.0.0.1:8080 是 AFS（内部名 chat2api）的**网页账号桥**——对面是
chatglm.cn / kimi 会员账号里的**托管智能体**，自带沙箱、自带系统提示、自带工具，
拿不到也不执行 DSH 的 39 个本地工具。实测：拿 DSH 真实请求重放会回 `{"name":"finish"}`。
它可以当"文本腿"，**不可以当 agent 脑**。

钉的是【规则】不是现场：
  - 默认脑必须解析得出非 loopback 的 baseURL（桥被设成脑 ⇒ 红）；
  - 桥地址只准出现在取证探针里，任何 skill/插件/文档硬编码它 = 长出第二条调用轨；
  - 官方 provider 注册表里真有 GLM/Kimi 裸模型入口 ⇒ "接云端 GLM"不需要装插件，只差一把 API key。
不钉的：AFS 进程活着没活着、托管开关在不在「关闭」——那是外部进程状态，钉了就是随机假红的假保险。
"""
import os
import pathlib
import re
import subprocess
import sys

TOTAL = 0
fails = []


def _safe(s):
    """GBK 控制台上 ⇒/🔴 会让 print 抛 UnicodeEncodeError 崩掉整个闸。
    🔴 只按输出流编码做 errors='replace'，绝不 sys.stdout.reconfigure —— 那会造成"零输出 + exit 0"的静默假绿。
    ⚠️ 也别写裸 s.encode()：str.encode() 默认 utf-8，永远不失败，等于没兜住。"""
    enc = getattr(sys.stdout, "encoding", None) or "ascii"
    try:
        s.encode(enc)
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode(enc, "replace").decode(enc, "replace")


def check(name, got, want):
    global TOTAL
    TOTAL += 1
    a = __import__("json").dumps(got, ensure_ascii=False, default=str)
    b = __import__("json").dumps(want, ensure_ascii=False, default=str)
    print(_safe(("PASS " if a == b else "FAIL ") + name + ("" if a == b else "  => %r (期望 %r)" % (got, want))))
    if a != b:
        fails.append(name)


PIAI = pathlib.Path(os.environ.get("DSH_PIAI_DATA") or
                    r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\profiles"
                    r"\node_modules\@earendil-works\pi-ai\dist\providers\data")
SETTINGS = pathlib.Path(os.environ.get("DSH_SETTINGS_YAML") or
                        r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\settings.yaml")
WS = pathlib.Path(r"C:\dsh")

try:
    import yaml as _yaml
    _st = _yaml.safe_load(SETTINGS.read_text(encoding="utf-8", errors="replace")) or {}
except Exception as e:                                    # noqa: BLE001
    _st = {"__error__": type(e).__name__ + ": " + str(e)[:120]}

prm = (_st.get("llm-pi-ai") or {}).get("providers") or {}
default = _st.get("agent-default-model") or {}
prov, mdl = default.get("provider"), default.get("model")
base = str(((prm.get(prov) or {}).get("baseURL")) or "")
LOOPBACK = re.compile(r"https?://(127\.0\.0\.1|localhost|0\.0\.0\.0)(:\d+)?")
bridge_ids = sorted({p for p, v in prm.items() if LOOPBACK.match(str(v.get("baseURL") or ""))})

check("L1 settings.yaml 可读，agent-default-model 的 provider 在 providers 里解析得到",
      [("__error__" in _st), bool(prov), bool(mdl), prov in prm], [False, True, True, True])
check("L2 默认脑不是账号桥（baseURL 不许 loopback）；桥 lane 名单照实报出",
      [prov, bool(LOOPBACK.match(base))], [prov, False])
check("L3 默认脑的 model id 不在任何桥 lane 的 models 里（防 'glm/Kimi3' 换写法回归）",
      [mdl in [m.get("id") for b in bridge_ids for m in (prm[b].get("models") or [])], bridge_ids],
      [False, bridge_ids])

# L4 复发闸：桥地址只准出现在取证探针（scripts/probe_*.py）里。
hits = []
for root in (".dsh/skills", "plugins", "docs", "scripts"):
    for p in (WS / root).rglob("*"):
        if not p.is_file() or p.suffix not in (".md", ".py", ".js", ".json", ".mjs"):
            continue
        if p.name.startswith("probe_") or p.name == os.path.basename(__file__):
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except Exception:                                  # noqa: BLE001
            continue
        if p.suffix in (".py", ".js", ".mjs"):             # 注释里提桥地址是"说明问题"，不算调用轨
            t = "\n".join(l for l in t.splitlines() if not l.strip().startswith(("#", "//")))
        elif p.suffix == ".md":
            # 文档**讲**这场事故必然要点名 8080（ADR 就是这么写的），所以 .md 只扫围栏代码块 ——
            # 围栏里的命令才是"教 skill 直接打桥"的第二条轨。
            t = "\n".join(re.findall(r"```[^\n]*\n(.*?)```", t, re.S))
        if re.search(r"https?://(127\.0\.0\.1|localhost):8080", t):
            hits.append(str(p.relative_to(WS)).replace("\\", "/"))
check("L4 skill/插件/文档零处硬编码 127.0.0.1:8080（模型调用唯一入口 = 网关，不许长出第二条轨）", hits, [])


def _reg(fname):
    p = PIAI / fname
    if not p.is_file():
        return {}
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                      # noqa: BLE001
        return {}


zai, moon = _reg("zai-coding-cn.json"), _reg("moonshotai-cn.json")
flat_zai = {k for v in zai.values() for k in v}
flat_moon = {k for v in moon.values() for k in v}
check("L5 官方 provider 注册表在位（zai-coding-cn / moonshotai-cn 两份都读得到 ⇒ 不用装插件）",
      [bool(flat_zai), bool(flat_moon)], [True, True])
_z = next((m for api in zai.values() for k, m in api.items() if k == "glm-5.3"), {})
_m = next((m for api in moon.values() for k, m in api.items() if k == "kimi-k3"), {})
check("L6 云端 GLM/Kimi 的裸模型入口已内置：glm-5.3 走 bigmodel.cn 且带 tool-stream 兼容",
      [_z.get("api"), _z.get("baseUrl"), ("zaiToolStream" in (_z.get("compat") or {})) and _z.get("baseUrl")],
      ["openai-completions", "https://open.bigmodel.cn/api/coding/paas/v4",
       "https://open.bigmodel.cn/api/coding/paas/v4"])
check("L7 moonshot 官方端点在册（kimi-k3 走 api.moonshot.cn，鉴权只要 MOONSHOT_API_KEY）",
      [_m.get("api"), str(_m.get("baseUrl", "")).startswith("https://api.moonshot.cn/")],
      ["openai-completions", True])
# ⚠️ 故意不钉的一条：桥 lane 将来若登记进网关（purpose afskimi/afsglm），计价 basis 绝不许是 usage
#    （实测 usage 恒为 prompt_tokens:1/completion_tokens:1 = 假计量）。那是**登记时**要写的断言，
#    现在写成"网关里不许出现 afskimi"就是钉现场 —— 做完正事闸反而长红，等于假保险。
print(_safe("\n=== agent 脑准入闸：%d 条断言，失败 %d ===" % (TOTAL, len(fails))))
if fails:
    print(_safe("失败清单: " + " | ".join(f.split()[0] for f in fails)))
sys.exit(1 if fails else 0)

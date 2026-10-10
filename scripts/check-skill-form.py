# -*- coding: utf-8 -*-
"""技能表单闸（2026-10-08）：钉 docs/ADR-one-shot-inchat-form.md 的三条铁律。

  A 组：字段表唯一源 = SKILL.md 的 form: 段（解析器判据 + 不许有第二份硬编）
  B 组：表单不直连引擎、不自动提交、不出现价格（扫渲染器源码）
  C 组：buildSkillDraft 纯函数值表（抠进 node 跑，不复制逻辑）
  D 组：字段必须对得上引擎真实入参（防"改了也不生效的控件"）

条数以本文件末尾打印的汇总为准。
"""
import base64
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.abspath(os.path.join(HERE, "..", "scratch", "skill-form-gate"))
os.makedirs(SCRATCH, exist_ok=True)

CLIENT = r"C:\dsh\plugins\super-clone-ui\lib\client.js"
SKILLS_DIR = r"C:\dsh\.dsh\skills"
GW = r"D:\video-skill"
# 🔴 2026-10-09 挂点改判：表单渲染器搬进 8100 的页面（backend/skill_form_page.py），
#    插件里【不该再有表单】。所以这个闸现在扫两个地方：页面里该有的必须全有，插件里不该有的必须全无。
PAGE = os.path.join(GW, "backend", "skill_form_page.py")

SRC = open(CLIENT, encoding="utf-8").read()
PAGE_SRC = open(PAGE, encoding="utf-8").read()
# 表单一律带注释头，剥掉注释再扫源码 —— 否则一条"不许 setDraft"的注释自己就能把断言喂绿
PAGE_CODE = "\n".join(l for l in PAGE_SRC.splitlines() if not l.strip().startswith("#"))
# 🔴 扫【不许出现】这类断言时必须先剥注释，而且只取模板正文：python 文档串里就写着"不 setDraft"，
#    拿整个 .py 去扫会被自己的注释喂红（或反过来，把违规写进注释里就喂绿）。
_mpage = re.search(r'FORM_PAGE = r"""(.*?)\n"""', PAGE_SRC, re.S)
PAGE_JS = "\n".join(l for l in ((_mpage.group(1) if _mpage else "")).splitlines()
                    if not l.strip().startswith("//"))
SKILL_MD = os.path.join(SKILLS_DIR, "one-shot-video", "SKILL.md")
MD = open(SKILL_MD, encoding="utf-8").read()


def skill_md_paths():
    out = []
    for name in sorted(os.listdir(SKILLS_DIR)):
        p = os.path.join(SKILLS_DIR, name, "SKILL.md")
        if os.path.isfile(p):
            out.append((name, p))
    return out

RESULTS = []


def ap(s):
    return str(s).encode("ascii", "replace").decode("ascii")


def cprint(*a, **k):
    try:
        print(*a, **k)
    except UnicodeEncodeError:
        print(ap(" ".join(str(x) for x in a)))


def chk(cid, desc, got, want):
    ok = got == want
    RESULTS.append((cid, ok))
    cprint("%s %-5s %-56s got=%s want=%s" % ("PASS" if ok else "FAIL", cid, ap(desc[:56]),
                                             ap(got), ap(want)))


# ---------- 解析器（服务端那份，直接 import 真代码，不复制） ----------
sys.path.insert(0, GW)
os.environ.setdefault("WEEKSIR_SKILLS_DIR", r"C:\dsh\.dsh\skills")
from backend import model_admin as MA  # noqa: E402

fields = MA.parse_skill_form(MD)
by_key = {f["key"]: f for f in fields}

cprint("== 环境 == 解析到 %d 个字段: %s" % (len(fields), list(by_key)))

# ---------- A 字段表唯一源（2026-10-09 泛化：不再钉某一个文件、不再钉"正好 6 个字段"） ----------
FORM_SKILLS = [(n, MA.parse_skill_form(open(p, encoding="utf-8", errors="replace").read()))
               for n, p in skill_md_paths()]
FORM_SKILLS = [(n, f) for n, f in FORM_SKILLS if f]
# 🔴 类型名单【读服务端那一份】，不在闸里再抄一遍：抄一份就会漂成"闸绿、服务端不收这个类型"。
ALL_TYPES = tuple(MA._FORM_TYPES)
MEDIA_TYPES = tuple(MA._FORM_MEDIA)
cprint("== 环境 == 带 form: 段的技能 %d 条：%s"
       % (len(FORM_SKILLS), ", ".join("%s(%d)" % (n, len(fs)) for n, fs in FORM_SKILLS)))

chk("A0", "至少有一条线配了字段表（一条都没有 = 这套机制是死的，别拿绿当数）",
    len(FORM_SKILLS) >= 1, True)
# 🔴 `locked` 行是【只读展示】，天生没有引擎入参 ⇒ 它不参与"param 必须齐/不许重复"这两条，
#    否则等于用"能填的字段"的标准去要求一个刻意不能填的东西（那正是它存在的理由）。
def _fillable(fs):
    return [f for f in fs if f["type"] != "locked"]


chk("A1", "每条线的字段都能解析出【六列齐全】的表（key/type/label/必填/选项/param）",
    [n for n, fs in FORM_SKILLS if any(not f["param"] or not f["label"] for f in _fillable(fs))], [])
chk("A2", "每条线必填项都非空（一张全是选填的表 = 客户填什么都可能跑不起来）",
    [n for n, fs in FORM_SKILLS if not any(f["required"] for f in fs)], [])
chk("A3", "type 只认服务端登记的那几种（写错的行解析阶段就被丢掉，不是猜出来）",
    {f["type"] for _, fs in FORM_SKILLS for f in fs} <= set(ALL_TYPES), True)
chk("A4", "同一张表里 param 不重复（两个字段写进同一个引擎入参 = 后一个悄悄覆盖前一个）",
    [n for n, fs in FORM_SKILLS
     if len({f["param"] for f in _fillable(fs)}) != len(_fillable(fs))], [])
chk("A5", "字段表里没有改了也不生效的控件（价格/积分字样一律不许）",
    [n for n, fs in FORM_SKILLS
     if any(re.search(r"price|credit|积分|cost", f["key"] + f["param"] + f["label"], re.I) for f in fs)],
    [])
chk("A6", "媒体字段的区间上限都解析出来了（没写就退到默认，不能是 None）",
    [f["key"] for _, fs in FORM_SKILLS for f in fs
     if f["type"] in MEDIA_TYPES and not f.get("max")], [])
chk("A7", "没有 form: 段的 skill ⇒ 空数组（不显示表单，不影响聊天照跑）",
    MA.parse_skill_form("---\nname: x\ndescription: y\n---\n正文"), [])
chk("A8", "列数不足/类型写错的行被丢弃（不臆造字段）",
    [f["key"] for f in MA.parse_skill_form(
        "---\nform: |\n  a|bogus|标签|no| |p\n  b|text|只有一列\n  c|text|好行|no| |c\n---\n正文")],
    ["c"])
# 泛化后的核心断言：加一条新线【不需要改这个闸】也照样被扫到
chk("A9", "表单页只打 8100 这一个口：基址出现一次，且页面里不许出现任何绝对主机地址",
    PAGE_JS.count('API = "/api/skills/form"') == 1 and "127.0.0.1" not in PAGE_JS
    and PAGE_JS.count("API") >= 3, True)
chk("A10", "A 组是枚举式的：每条带表的线都被上面那几条扫过（加线不用改这个闸）",
    len(FORM_SKILLS) >= 1 and all(isinstance(fs, list) and fs for _, fs in FORM_SKILLS), True)

# ---------- B 挂点与铁律（2026-10-09 改判：渲染器在 8100 的页面里，插件里【不该再有表单】） ----------
# B1-B11 扫页面正文（注释已剥），B12-B14 扫插件（确认整块下线、没留第二个入口）
chk("B1", "🔴 表单页不调任何引擎口（8090/8765/8091/gateway 全不许出现）",
    bool(re.search(r":8090|:8765|:8091|gateway\.py|/api/one-shot|/api/run/", PAGE_JS)), False)
chk("B2", "🔴 一寸不碰 composer：setDraft / addAttachments / inputActions 全不许出现",
    bool(re.search(r"setDraft|addAttachments|inputActions", PAGE_JS)), False)
chk("B3", "🔴 不出现输入框覆盖层槽名（conversation.input.overlay 永久禁止）",
    "conversation.input.overlay" in PAGE_SRC, False)
chk("B4", "提交口 = 8100 的 /api/skills/form 一个地址（同源相对路径，右栏 iframe 不跨口）",
    PAGE_JS.count('"/api/skills/form"') >= 1 and "http://127.0.0.1:8100" not in PAGE_JS, True)
chk("B5", "🔴 意图门控：show=false ⇒ 整块不画（新会话一条 UI 都不出，表也藏起来）",
    ("!j.show" in PAGE_JS) and ("getElementById(\"empty\")" in PAGE_JS), True)
chk("B6", "🔴 提交必须是简单请求（8100 没有 do_OPTIONS，application/json 会预检撞 501）",
    "text/plain;charset=UTF-8" in PAGE_JS and '"application/json"' not in PAGE_JS, True)
chk("B7", "页面里不出现积分/价格字样（点头只有 /approval 那一次点击）",
    bool(re.search(r"积分|credits|/段|¥|扣多少分", PAGE_JS)), False)
chk("B8", "8100 没回话要说成故障，不能让人误读成『没有表单/技能没配』",
    ("8100 没回话" in PAGE_SRC) and ("不是【没有表单】" in PAGE_SRC or "不是" in PAGE_SRC), True)
chk("B9", "🔴 灰态不哑：未填完仍可点，且必须报出缺哪几格（不许静默）",
    ("miss.length" in PAGE_JS) and ("data-miss" in PAGE_JS) and ("scrollIntoView" in PAGE_JS), True)
chk("B10", "🔴 回填值要标出来（AI 已填 · 可改）并给依据，且回填值计入完成度",
    ("AI 已填" in PAGE_SRC) and ("defaults" in PAGE_JS) and ("default_files" in PAGE_JS), True)
chk("B11", "已提交 = 转确认态列出最终参数（客户看得见自己交了什么）",
    "showDone" in PAGE_JS, True)
chk("B12", "🔴 插件侧：对话流里【没有任何技能表单】（turnTail 整块下线）",
    bool(re.search(r"mountSkillForm|buildFormPayload|SKILL_FORM_API", SRC)), False)
chk("B13", "插件侧：也不许出现输入框覆盖层/表单卡的残留槽名",
    bool(re.search(r"conversation\.chat\.turnTail[\s\S]{0,400}form", SRC, re.I)), False)
chk("B14", "右栏那一档的 url 由判定口给（插件不硬编第二份端口/技能名）",
    "weeksir-skill-form-view" in open(r"C:\dsh\plugins\ops-gateway\lib\client.js",
                                      encoding="utf-8").read(), True)

# ---------- C 提交按钮完成判据 formIsComplete 的纯函数值表（从页面里抠出来，进 node 真跑） ----------
# 🔴 起点是 isMedia 不是 formIsComplete：判据现在共用这一个函数，只抠后半段会在 node 里 ReferenceError。
a = PAGE_CODE.index("function isMedia")
# 🔴 切片终点必须落在 formIsComplete 自己的收尾上。以前取"a 之后第一个 \n}"，
#    我在两者之间加了 isShown/isLocked 之后，那个终点就变成了 isShown 的收尾
#    ⇒ 抠出去的一段里根本没有 formIsComplete，node 直接崩（这条就是那次崩的记录）。
_b0 = PAGE_CODE.index("function formIsComplete", a)
b = PAGE_CODE.index("\n}", _b0) + 2
JS = PAGE_CODE[a:b] + """
const F = [
  {key:"txt", type:"textarea", label:"一句话意图", required:true, param:"user_intent"},
  {key:"vid", type:"video", label:"爆款视频", required:true, param:"video_path", max:1},
  {key:"imgs", type:"images", label:"商品图", required:false, param:"product_image_paths", max:7},
  {key:"dur", type:"number", label:"目标时长", required:false, param:"duration", min:5, max:30},
  {key:"eng", type:"select", label:"引擎", required:false, param:"engine", options:["gpt 默认","grok 备选"]},
];
// 音频槽单开一张表：塞进 F 会把 1~10 条的期望值全改掉，那是改判据不是加判据。
const FA = F.concat([{key:"aud", type:"audio", label:"目标人声", required:true,
                      param:"voice_audio_paths", max:1}]);
function G(extra){ const g = Object.assign({skill:"x", fields:F}, extra||{}); return g; }
function GA(extra){ const g = Object.assign({skill:"x", fields:FA}, extra||{}); return g; }
const out = [];
// 1 空表 ⇒ 灰，且缺的是那两个必填
out.push(formIsComplete(G(), {}, {}, {}).map(f => f.label));
// 2 只填文本、必填视频没交 ⇒ 仍灰
out.push(formIsComplete(G(), {txt:"泡脚桶"}, {}, {}).map(f => f.label));
// 3 必填齐 ⇒ 黄
out.push(formIsComplete(G(), {txt:"泡脚桶"}, {vid:[{name:"a.mp4"}]}, {}).map(f => f.label));
// 4 number 越界 ⇒ 灰（只判非空就会变成"按钮黄了、提交被服务端拒"）
out.push(formIsComplete(G(), {txt:"x", dur:"900"}, {vid:[{}]}, {}).map(f => f.label));
// 5 number 非数字 ⇒ 灰
out.push(formIsComplete(G(), {txt:"x", dur:"十五"}, {vid:[{}]}, {}).map(f => f.label));
// 6 非必填全空 ⇒ 黄（不能拿选填去卡客户）
out.push(formIsComplete(G(), {txt:"x"}, {vid:[{}]}, {}).map(f => f.label));
// 7 🔴 全靠 agent 回填补齐 ⇒ 必须黄（回填了还是灰 = 白回）
out.push(formIsComplete(G({default_files:{video_path:"C:\\\\a.mp4"}}), {}, {},
                        {txt:"从客户上传的图里读到", dur:"20"}).map(f => f.label));
// 8 回填补了必填视频、但必填文本没回填 ⇒ 仍灰
out.push(formIsComplete(G({default_files:{video_path:"C:\\\\a.mp4"}}), {}, {}, {}).map(f => f.label));
// 9 门控残缺不许炸（闸自己不许成为故障点）
out.push(formIsComplete(null, {}, {}, {}));
// 10 回填的数字【越界】⇒ 灰：证明 defaults 也过同一套区间判据，不是"有值就放行"
out.push(formIsComplete(G(), {txt:"x"}, {vid:[{}]}, {dur:"900"}).map(f => f.label));
// 11~13 音频槽（第七种类型）：必须被当【文件】判，不能退化成"填了字就算"
out.push(formIsComplete(GA(), {txt:"x"}, {vid:[{}]}, {}).map(f => f.label));
out.push(formIsComplete(GA(), {txt:"x"}, {vid:[{}], aud:[{name:"v.mp3"}]}, {}).map(f => f.label));
out.push(formIsComplete(GA({default_files:{video_path:"C:\\\\a.mp4",
                        voice_audio_paths:["C:\\\\v.mp3"]}}), {txt:"x"}, {}, {}).map(f => f.label));
// 14~16 条件显隐 + 🔒 只读行：14 与 15 【只差 show_if 那一条】，用来证明"藏起来就不卡、露出来才卡"
const FS = [
  {key:"txt", type:"textarea", label:"必填文本", required:true, param:"p1"},
  {key:"vm", type:"select", label:"声音", required:true, param:"voice_mode",
   options:["preserve_original 原声保留", "timbre_swap 只换音色"]},
  {key:"aud", type:"audio", label:"参考音频", required:true, param:"voice_audio", max:1,
   show_if:{key:"vm", values:["timbre_swap"]}},
  {key:"lk", type:"locked", label:"生成引擎", required:true, param:"", value:"wan3"}
];
function GS(){ return {skill:"x", fields:FS}; }
out.push(formIsComplete(GS(), {vm:"preserve_original 原声保留"}, {}, {}).map(f => f.label));
out.push(formIsComplete(GS(), {vm:"timbre_swap 只换音色"}, {}, {}).map(f => f.label));
out.push(formIsComplete(GS(), {vm:"timbre_swap 只换音色"}, {aud:[{name:"a.mp3"}]}, {}).map(f => f.label));
console.log(JSON.stringify(out));
"""
JSF = os.path.join(SCRATCH, "_draft.cjs")
open(JSF, "w", encoding="utf-8", newline="\n").write(JS)
data = None
try:
    r = subprocess.run(["node", JSF], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    for line in reversed([l for l in (r.stdout or "").splitlines() if l.strip()]):
        try:
            p = json.loads(line)
        except ValueError:
            continue
        if isinstance(p, list):
            data = p
            break
    if data is None:
        cprint("FAIL C0 node 没跑出结果 | rc=%s err=%s" % (r.returncode, ap((r.stderr or "")[:200])))
except Exception as e:
    cprint("FAIL C0 node 跑不了 | %s" % ap(str(e)[:120]))

if data:
    chk("C1", "空表 ⇒ 灰，且缺的就是那两个必填（报得出具体的格）", data[0], ["一句话意图", "爆款视频"])
    chk("C2", "只填文本、必填视频没交 ⇒ 仍灰", data[1], ["爆款视频"])
    chk("C3", "必填齐 ⇒ 黄", data[2], [])
    chk("C4", "🔴 number 越界 ⇒ 灰（只判非空就会变成「按钮黄了、提交却被服务端拒」）", data[3], ["目标时长"])
    chk("C5", "number 非数字 ⇒ 灰（不悄悄放过）", data[4], ["目标时长"])
    chk("C6", "选填全空 ⇒ 黄（不许拿选填卡客户）", data[5], [])
    chk("C7", "🔴 全靠 agent 回填补齐 ⇒ 必须黄（回填了还灰着 = 白回）", data[6], [])
    chk("C8", "反向对照：只回填了视频、必填文本没回填 ⇒ 仍灰（C7 不是因为恒黄而绿）",
        data[7], ["一句话意图"])
    chk("C9", "门控残缺也不炸（闸自己不许成为故障点）", data[8], [])
    chk("C10", "回填的数字越界 ⇒ 灰（证明 defaults 也过区间判据，不是有值就放行）",
        data[9], ["目标时长"])
    chk("C11", "🔴 音频槽没选 ⇒ 灰并报出「目标人声」（证明 audio 被当【文件】判，不是退化成文本框）",
        data[10], ["目标人声"])
    chk("C12", "音频选了 ⇒ 黄（能被判成已填，否则这格永远点不动提交）", data[11], [])
    chk("C13", "音频由 agent 回填 ⇒ 也黄（回填链路对第七种类型同样生效，不是只给图/视频开）",
        data[12], [])
    # 跨语言一致性：JS 的 isMedia 与服务端 _FORM_MEDIA 必须是同一份名单。
    # 漂了的形状是"服务端收音频、前端当文本渲染"或反之 —— 两边各自都绿，客户那边提交才炸。
    _py_media = set(MA._FORM_MEDIA)   # 直接读服务端那份，不拿正则再抄一遍判据
    _im = PAGE_CODE.index("function isMedia")
    _js_media = set(re.findall(r'f\.type === "(\w+)"',
                               PAGE_CODE[_im:PAGE_CODE.index("\n", _im)]))   # 只取那一行
    chk("C14", "🔴 前端 isMedia 与服务端 _FORM_MEDIA 同名单（漂了=一边收一边不收，提交才炸）",
        sorted(_js_media), sorted(_py_media))
    chk("C15", "🔴 条件显隐：选了「原声保留」⇒ 隐藏的参考音频【不卡提交】，🔒 行也不卡",
        data[13], ["必填文本"])
    chk("C16", "反向对照：同一张表只把档换成「只换音色」⇒ 参考音频立刻变成缺项（C15 不是恒放行）",
        data[14], ["必填文本", "参考音频"])
    chk("C17", "露出来且给了文件 ⇒ 不卡（证明 C16 报的是「没给」，不是「这一档永远填不满」）",
        data[15], ["必填文本"])
else:
    for cid in ("C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10"):
        RESULTS.append((cid, False))
    cprint("FAIL C1-C10 纯函数未执行 ⇒ 整组按失败计（不许静默跳过）")

# ---------- E 服务端提交口（真跑 form_submit，全部落在临时目录） ----------
# 这一组钉的是用户那句"填写收集到的能正常入参"：键名必须是引擎 param、必填必须真拦、
# 没登记意图必须拒、文件必须洗成 ASCII 且只能落在写死的目录里。
import shutil
import time as _t

FIELDS = [
    {"key": "user_intent", "type": "textarea", "label": "一句话意图", "required": True,
     "options": [], "param": "user_intent"},
    {"key": "product_images", "type": "images", "label": "商品图", "required": False,
     "options": [], "param": "product_image_paths"},
    {"key": "variant_strategy", "type": "select", "label": "出几版", "required": False,
     "options": ["all 三变体", "A 痛点"], "param": "variant_strategy"},
    {"key": "engine", "type": "select", "label": "引擎", "required": False,
     "options": ["gpt 默认", "grok 备选"], "param": "engine"},
]
SANDBOX = os.path.join(SCRATCH, "sandbox")
# 🔴 自测后门只许对临时目录有效：跑之前记下【真】载荷的存在与 mtime，跑完必须一模一样。
def _probe(path):
    try:
        return (True, os.stat(path).st_mtime_ns)
    except OSError:
        return (False, 0)
shutil.rmtree(SANDBOX, ignore_errors=True)
os.makedirs(SANDBOX)
_real = (MA.FORM_PAYLOAD_PATH, MA.FORM_UPLOAD_DIR, MA.skill_form_index, MA.hypit_bridge.intent_map)
PROD_BEFORE = _probe(_real[0])   # 打补丁之前先记下真载荷的样子
try:
    MA.FORM_PAYLOAD_PATH = os.path.join(SANDBOX, "form_payload.json")
    MA.FORM_UPLOAD_DIR = os.path.join(SANDBOX, "uploads")
    MA.skill_form_index = lambda: {"product-video": {"skill": "one-shot-video", "fields": FIELDS}}
    LIVE = {"session-live": {"skill": "product-video", "task": "", "ts": _t.time()}}
    MA.hypit_bridge.intent_map = lambda: dict(LIVE)

    r1 = MA.form_submit({"session": "session-none", "skill": "one-shot-video",
                         "values": {"user_intent": "x"}})
    chk("E1", "🔴 这条会话没登记意图 ⇒ 拒（先明确意图，才有表单）", r1.get("error"),
        "no-intent-for-this-skill")
    chk("E1b", "被拒时载荷文件根本没建（不落半份参数）", os.path.exists(MA.FORM_PAYLOAD_PATH), False)

    r2 = MA.form_submit({"session": "session-live", "skill": "one-shot-video", "values": {}})
    chk("E2", "必填没填 ⇒ 拒，并说是哪一项", r2.get("error"), "必填没填：一句话意图")

    r3 = MA.form_submit({"session": "session-live", "skill": "one-shot-video",
                         "values": {"user_intent": "  泡脚桶 15 秒  ", "variant_strategy": "A 痛点",
                                    "engine": "gpt 默认", "made_up_key": "别臆造入参"}})
    chk("E3", "params 的键 = 字段表里的引擎 param 名（不是前端 key）",
        sorted(r3.get("params") or {}), ["engine", "user_intent", "variant_strategy"])
    chk("E4", "select 归一成选项码（A 痛点 → A）", r3["params"].get("variant_strategy"), "A")
    chk("E5", "🔴 字段表里没有的 key 直接丢（不臆造成引擎入参）",
        any("made_up" in k for k in r3["params"]), False)
    chk("E6", "值首尾空白被清", r3["params"].get("user_intent"), "泡脚桶 15 秒")
    chk("E7", "提交成功后门控说 submitted=true、回显 params",
        (MA.form_gate_view("session-live") or {}).get("submitted"), True)
    chk("E8", "意图过期 ⇒ 载荷不再算数（拿几天前的表去入参 = 事故）",
        MA._form_payload_live({
            "session-live": {"skill": "one-shot-video", "params": {"user_intent": "x"},
                             "ts": _t.time() - 99 * 3600}}, "session-live"), {})
    chk("E9", "意图档没配 form 的 skill ⇒ show=false（不猜、不借别人的表）",
        MA.form_gate_view("session-live")["show"], True)

    bad = MA.form_submit({"session": "session-live", "skill": "one-shot-video",
                          "values": {"user_intent": "x"},
                          "files": [{"name": "a.txt", "type": "text/plain", "data": "aGk="}]})
    chk("E10", "非图片类型 ⇒ 拒（不收任意文件）", bool(bad.get("error")), True)
    okf = MA.form_submit({"session": "session-live", "skill": "one-shot-video",
                          "values": {"user_intent": "x"},
                          "files": [{"name": "../../evil.png", "type": "image/png",
                                     "data": "iVBORw0KGgo="}]})
    p = (okf.get("params") or {}).get("product_image_paths") or [""]
    chk("E11", "文件名被洗成 ASCII 且只能落在写死的上传目录里（防 ../ 穿越）",
        p[0].startswith(os.path.join(SANDBOX, "uploads")) and ".." not in os.path.basename(p[0]),
        True)
    chk("E12", "图片按字段声明的 param 名进 params（product_images → product_image_paths）",
        "product_image_paths" in (okf.get("params") or {}), True)
    chk("E13", "超过 7 张 ⇒ 拒（与 SKILL.md 的 1~7 张同口径）",
        bool(MA.form_submit({"session": "session-live", "skill": "one-shot-video",
                             "values": {"user_intent": "x"},
                             "files": [{"name": "a.png", "type": "image/png",
                                        "data": "iVBORw0KGgo="}] * 8}).get("error")), True)
    chk("E14", "空文件/超大文件 ⇒ 拒", bool(MA.form_submit(
        {"session": "session-live", "skill": "one-shot-video", "values": {"user_intent": "x"},
         "files": [{"name": "a.png", "type": "image/png", "data": ""}]}).get("error")), True)
finally:
    (MA.FORM_PAYLOAD_PATH, MA.FORM_UPLOAD_DIR, MA.skill_form_index,
     MA.hypit_bridge.intent_map) = _real
    shutil.rmtree(SANDBOX, ignore_errors=True)
chk("E15", "🔴 自测只打在临时目录上：真载荷文件的存在与 mtime 一字未动",
    _probe(_real[0]), PROD_BEFORE)

# ---------- D 字段必须对得上引擎入参 ----------
COMPILE_PARAMS = {"user_intent", "product_image_path", "product_image_paths", "product_info",
                  "user_supplement", "variant_count", "variant_strategy", "engine"}
bad = [f["param"] for f in fields if f["param"] not in COMPILE_PARAMS]
chk("D1", "每个 param 都是 /api/one-shot/compile 的真实入参", bad, [])
forbidden = [f["key"] for f in fields if re.search(r"duration|resolution|aspect|ratio|price|credit", f["key"], re.I)]
chk("D2", "不许出现改了也不生效的控件（时长/比例/分辨率/价格）", forbidden, [])

# ---------- D3~D5 super-clone 的表对到【它自己的】入口（不是 one-shot 的那套） ----------
# 这条表是新加的，最容易坏成两种：param 写成引擎没有的名字（客户填了不生效），
# 或者选项自己抄一份（上游加一档，这里就少一档 —— 而两边各自都绿）。
SC_MD = open(os.path.join(SKILLS_DIR, "super-clone", "SKILL.md"), encoding="utf-8").read()
SC_FIELDS = MA.parse_skill_form(SC_MD)
SC_RUN = open(os.path.join(SKILLS_DIR, "super-clone", "runner", "run.py"),
              encoding="utf-8").read()
RUN_ARGS = {m.replace("-", "_") for m in re.findall(r'add_argument\("--([a-z-]+)"', SC_RUN)}
# 只有这一个字段是【agent 读的】，不是引擎入参：它属于"换流程"那条线，写进正文第 2 条口径。
SC_AGENT_ONLY = {"voice_script"}
chk("D3", "🔴 super-clone 每个【可填】param 都是 runner 的真参数名（或显式声明的 agent-only 字段）",
    sorted({f["param"] for f in SC_FIELDS if f["type"] != "locked"} - RUN_ARGS - SC_AGENT_ONLY), [])
chk("D3b", "反向对照：真参数名集合不是空的（空集会让 D3 因为【减完还剩空】而假绿）",
    len(RUN_ARGS) >= 12, True)
_voice_opts = [o.split(" ")[0] for o in
               next((f["options"] for f in SC_FIELDS if f["key"] == "voice_mode"), [])]
chk("D4", "声音三档的选项 == runner 的 VOICE_MODES 唯一源（漂了就是一边能选一边被拒）",
    _voice_opts, re.findall(r'"(\w+)"', re.search(r"VOICE_MODES\s*=\s*\(([^)]*)\)", SC_RUN).group(1)))
_gw = open(os.path.join(GW, "backend", "gateway.py"), encoding="utf-8", errors="replace").read()
_ratio_opts = [o.split(" ")[0] for o in
               next((f["options"] for f in SC_FIELDS if f["key"] == "aspect_ratio"), [])]
chk("D5", "🔴 画面比例的选项 == 网关 _PRIME_YZ_RATIOS（含顺序）：名单只在网关那一份",
    _ratio_opts, [x.strip().strip('"\'') for x in
                  re.search(r"_PRIME_YZ_RATIOS\s*=\s*\(([^)]*)\)", _gw).group(1).split(",")
                  if x.strip()])
chk("D5b", "反向对照：比例选项不是空表（空表会让 D5 两边都空而绿）", len(_ratio_opts) >= 4, True)
chk("D6", "super-clone 的 intent 键对得上档名（挂错档 = 弹别人的台或不弹）",
    MA.parse_skill_intent(SC_MD), "super-clone")
chk("D8", "🔴 值变化必须重算显隐：refresh() 里要调 applyVis()"
    "（漏了的形状=点 chip 后条件字段永远不出现，实测踩过；页面只在打开时算一次是不够的）",
    "applyVis()" in PAGE_CODE[PAGE_CODE.index("function refresh("):
                     PAGE_CODE.index("}", PAGE_CODE.index("function refresh("))], True)
chk("D8b", "反向对照：applyVis 确实是个真函数且被 render 收尾调用（不是空壳被 grep 到）",
    [PAGE_CODE.count("function applyVis("), PAGE_CODE.count("applyVis();")], [1, 2])

# ---- D9 表单前置：入口必须拒掉「右栏那张表从没出现过」的会话 ----
# 现网事故形状（2026-10-10）：三条 super-clone 会话全部 await-prefill ⇒ 表单一张都没弹过，
# 因为回填是一条【可以跳过的额外命令】。这条判据绑到必然发生的动作（走入口出片）上，
# 并且真调函数跑值表 —— grep 到函数名不等于它会拒。
_sc_ns = {"__name__": "sc_runner_selftest"}
exec(compile(SC_RUN, "runner/run.py", "exec"), _sc_ns)
_fg = _sc_ns["form_gate_verdict"]
chk("D9", "🔴 入口判据值表：没登记/已提交/已回填(值或素材) ⇒ 放行；登记了但表没出现过 ⇒ 拒"
    "（恒放行=这条门槛根本不存在）",
    [bool(_fg(g)) for g in ({"show": False}, {"show": True, "submitted": True},
                            {"show": True, "defaults": {"product_name": "x"}},
                            {"show": True, "default_files": {"video": ["p"]}},
                            {"show": True}, None, 7, "")],
    [False, False, False, False, True, False, False, False])
chk("D9c", "拒绝文案必须带下一步命令（只说「不行」= agent 会另想办法绕过去）",
    "intent.py prefill" in _fg({"show": True}), True)
_m = SC_RUN[SC_RUN.index("def main("):]


def _at(hay, needle):
    """闸自己不许成为故障点：找不到就回 -1 让断言判红，而不是抛 ValueError 吃成崩溃。"""
    return hay.index(needle) if needle in hay else -1


chk("D9d", "🔴 这道门槛必须落在【导入引擎之前】（先跑再拦=已经动过盘、可能已经花过分）",
    0 <= _at(_m, "form_gate_verdict(") < _at(_m, "_ensure_video_skill_on_path()"), True)
_rg = _at(_m, "read_form_gate(")
chk("D9e", "读不到会话 id ⇒ 明说不判（绝不拿空 id 去查、也绝不猜一个填进去）",
    ("读不到会话 id" in _m[:_rg]) if _rg >= 0 else False, True)

# ---- D7 缩略图显示名：盘上带 sha8，界面上必须剥掉（不剥就变成 `01-40fd465c-`，客户认不出自己的文件）----
_a = PAGE_CODE.index("function dispName")
_b = PAGE_CODE.index("\n}", _a) + 2
_DJS = PAGE_CODE[_a:_b] + """
const cases = [
  ["C:\\\\dsh\\\\productions\\\\t\\\\inputs\\\\video\\\\01-40fd465c-original.mp4", "01-original.mp4"],
  ["03-d8740887-cardigan.png", "03-cardigan.png"],
  ["C:/x/inputs/02-8a2a4217-face.jpg", "02-face.jpg"],
  ["01-video.mp4", "01-video.mp4"],
  ["01-abcdefgh-name.png", "01-abcdefgh-name.png"],
  ["", ""]
];
// 第 7 条是【故意写错的期望】：它必须被判 False，否则说明这套比对根本不看结果（假绿）。
const bad = [["01-40fd465c-x.png", "01-40fd465c-x.png"]];
console.log(JSON.stringify({ok: cases.map(c => [dispName(c[0]), c[1], dispName(c[0]) === c[1]]),
                            bad: bad.map(c => [dispName(c[0]), c[1], dispName(c[0]) === c[1]])}));
"""
_DF = os.path.join(SCRATCH, "_dispname.cjs")
open(_DF, "w", encoding="utf-8", newline="\n").write(_DJS)
_dr = subprocess.run(["node", _DF], capture_output=True, text=True, encoding="utf-8",
                     errors="replace", timeout=60)
try:
    _do = json.loads([l for l in (_dr.stdout or "").splitlines() if l.strip()][-1])
    _dd = _do.get("ok") or []
except Exception:
    _do, _dd = None, None
chk("D7", "🔴 dispName 值表：剥 sha8、旧名不动、非哈希段不误伤、正反斜杠都取 basename（6/6 全对）",
    [len(_dd) if _dd else 0, all(x[2] for x in _dd) if _dd else False], [6, True])
chk("D7b", "反向对照：故意喂一条【不剥哈希】的期望 ⇒ 必须被判 False（证明 D7 真的在比结果）",
    [len(_do.get("bad") or []), (_do.get("bad") or [[None]])[0][2]] if _do else [0, None], [1, False])

# ---------- I 任务目录规范（2026-10-10 用户拍：inputs/src 分开、先只推超级复刻、sha8 进文件名）----------
INTENT_PY = open(os.path.join(HERE, "intent.py"), encoding="utf-8").read()
# 🔴 扫"不许出现"这类断言必须先剥注释：我在新代码的注释里写了那个废弃路径名，
#    不剥注释这条就会自己把自己扫红（本项目踩过一次，今天又踩一次）。
INTENT_CODE = "\n".join(l for l in INTENT_PY.splitlines() if not l.strip().startswith("#"))
chk("I1", "🔴 intent.py 里不许再拼一份上传路径（`agent/<param>` 那个跨会话共享目录就是这么长出来的）",
    "form-uploads" in INTENT_CODE, False)
chk("I2", "推行范围只有 super-clone（hypit 传的是 .svrun 工程路径、语义不同；别的线没拍就不动）",
    re.search(r"TASK_DIR_LANES\s*=\s*\(([^)]*)\)", INTENT_PY).group(1)
    .replace('"', "").replace(" ", "").rstrip(","), "super-clone")
chk("I2b", "子目录一次建全：inputs / src / outputs / frames（inputs=客户与回填素材，src=agent 派生物）",
    re.search(r"TASK_SUBDIRS\s*=\s*\(([^)]*)\)", INTENT_PY).group(1)
    .replace('"', "").replace(" ", "").rstrip(","), "inputs,src,outputs,frames")
# slug 规则【开子进程跑】：不能 import intent.py —— 它一 import 就 reconfigure 本进程 stdout，
# 那正是"闸自己变成故障点 / 零输出假绿"的形状（这条坑本项目踩过）。
_slug_body = INTENT_PY[INTENT_PY.index("def task_slug"):INTENT_PY.index("def ensure_task_dir")]
_sf = os.path.join(SCRATCH, "_slug_probe.py")
open(_sf, "w", encoding="utf-8").write(
    "import json,re,time\nWORKSPACE=r'C:\\dsh'\n" + _slug_body +
    "\nprint(json.dumps([task_slug('super-clone','超级复刻-大衣换脸换衣服'),"
    " task_slug('super-clone','dapin-coat'), task_slug('super-clone','ab'),"
    " task_slug('super-clone','../../etc/passwd')], ensure_ascii=True))\n")
_sr = subprocess.run([sys.executable, _sf], capture_output=True, text=True, timeout=60)
try:
    _slugs = json.loads([l for l in (_sr.stdout or "").splitlines() if l.strip()][-1])
except Exception:
    _slugs = None
if _slugs is None:
    chk("I3", "子进程没跑出 slug 结果 ⇒ 算红（闸不许把异常吞成通过）", str(_sr.stderr)[:120], "")
else:
    chk("I3", "任务名一律 ASCII 化：中文/太短/路径穿越都退到 `<skill>-<日期>`，绝不带 .. 或分隔符",
        [bool(re.fullmatch(r"super-clone-\d{8}-\d{4}", _slugs[0])), _slugs[1],
         bool(re.fullmatch(r"super-clone-\d{8}-\d{4}", _slugs[2])), _slugs[3]],
        [True, "dapin-coat", True, "etcpasswd"])
os.remove(_sf)


# ---------- G 组：本轮新能力（number/video 类型、多文件槽、agent 回填） ----------
# 走【真实链路】：临时技能目录 → parse_skill_form/parse_skill_intent → skill_form_index
# → form_submit / form_prefill → form_gate_view。不 mock 中间任何一环，mock 只能换传输层。
G_MD = """---
name: probe-skill
intent: product-video
form: |
  vid|video|爆款视频|yes|1|video_path|1KB
  product_images|images|产品图|no|1-10|product_image_paths
  model_image|images|面部图|no|1|model_image_path
  duration|number|目标时长|no|10-90|duration
  engine|select|推理引擎|no|gpt 默认;grok 备选|engine
  voice|audio|目标人声|no|1|voice_audio_paths
  bogus|nonsense|坏行|no| |p
---
正文
"""
GSB = os.path.join(SCRATCH, "gsandbox")
shutil.rmtree(GSB, ignore_errors=True)
os.makedirs(os.path.join(GSB, "probe-skill"))
open(os.path.join(GSB, "probe-skill", "SKILL.md"), "w", encoding="utf-8").write(G_MD)
g_payload = os.path.join(GSB, "form_payload.json")
g_prefill = os.path.join(GSB, "form_prefill.json")
PROD_PREFILL_BEFORE = _probe(MA.FORM_PREFILL_PATH)
_real_g = (MA.SKILLS_DIR, MA.FORM_PAYLOAD_PATH, MA.FORM_UPLOAD_DIR, MA.FORM_PREFILL_PATH,
           MA.hypit_bridge.intent_map)
try:
    MA.SKILLS_DIR = GSB
    MA.FORM_PAYLOAD_PATH = g_payload
    MA.FORM_PREFILL_PATH = g_prefill
    MA.FORM_UPLOAD_DIR = os.path.join(GSB, "uploads")
    # 可变字典：后面的用例要【换技能档】做对照，不能靠重新赋值 lambda（那会覆盖掉另一条会话）
    _G = {"g-live": {"skill": "product-video", "task": "", "ts": _t.time()},
          "g-pre": {"skill": "product-video", "task": "", "ts": _t.time()}}
    MA.hypit_bridge.intent_map = lambda: dict(_G)
    idx = MA.skill_form_index()
    gf = (idx.get("product-video") or {}).get("fields") or []
    gk = {f["key"]: f for f in gf}

    chk("G1", "类型只认服务端登记的那几种：video/audio/number 解析成功，坏类型行被丢（不臆造字段）",
        sorted(gk), ["duration", "engine", "model_image", "product_images", "vid", "voice"])
    chk("G2", "第 5 列在 number/images/video 上复用为区间：10-90 / 1-10 / 1",
        [(gk["duration"]["min"], gk["duration"]["max"]), (gk["product_images"]["min"],
                                                          gk["product_images"]["max"]),
         (gk["vid"]["min"], gk["vid"]["max"])], [(10, 90), (1, 10), (None, 1)])
    chk("G3", "intent: 段把表对到技能档（唯一源仍是 SKILL.md，这里没第二份映射）",
        (idx.get("product-video") or {}).get("skill"), "probe-skill")

    PNG = {"name": "p.png", "type": "image/png", "data": "iVBORw0KGgo="}
    MP4 = {"name": "v.mp4", "type": "video/mp4", "data": "AAAA"}
    r = MA.form_submit({"session": "g-live", "skill": "probe-skill",
                        "values": {"duration": "20", "engine": "grok 备选"},
                        "files": [dict(MP4, field="vid"), dict(PNG, field="product_images"),
                                  dict(PNG, field="model_image")]})
    chk("G4", "🔴 多文件槽：两个 images 槽各进【自己的】引擎 param（不再只认第一槽）",
        sorted(k for k in ("product_image_paths", "model_image_path")
               if k in (r.get("params") or {})), ["model_image_path", "product_image_paths"])
    p_i = (r["params"].get("product_image_paths") or [""])[0]
    p_m = (r["params"].get("model_image_path") or [""])[0]
    chk("G5", "两个槽同名文件不互相覆盖（文件名同、父目录不同）",
        (p_i != p_m and os.path.basename(p_i) == os.path.basename(p_m)
         and os.path.dirname(p_i) != os.path.dirname(p_m)), True)
    chk("G6", "video 上限 1 ⇒ 落【字符串】（引擎收 video_path，不是数组）",
        isinstance(r["params"].get("video_path"), str), True)
    # 🔴 第七种类型 audio 的端到端：不是"解析出来了"就算数，要真落盘 + 白名单真的按槽生效。
    MP3 = {"name": "v.mp3", "type": "audio/mpeg", "data": "AP+/"}
    # 必填的 vid 每次都要带上：漏了就是"必填没填"，拿不到 params，断言会崩而不是红。
    ra = MA.form_submit({"session": "g-live", "skill": "probe-skill",
                         "values": {"duration": "20"},
                         "files": [dict(MP4, field="vid"), dict(MP3, field="voice")]})
    va = (ra.get("params") or {}).get("voice_audio_paths") or []
    chk("G6b", "audio 槽收 mp3 ⇒ 落【数组】（引擎 --voice-audio 是 append）且文件真在盘上",
        [isinstance(va, list) and len(va) == 1 and os.path.isfile(va[0]),
         os.path.splitext(va[0])[1].lower() if va else "?"], [True, ".mp3"])
    rb = MA.form_submit({"session": "g-live", "skill": "probe-skill",
                         "values": {"duration": "20"},
                         "files": [dict(MP4, field="vid"), dict(PNG, field="voice")]})
    _rerr = str(rb.get("error") or "")
    chk("G6c", "🔴 反向对照：PNG 塞进 audio 槽 ⇒ 拒，且拒因点名【这个槽只收什么】+【收到的是什么】",
        [rb.get("ok"), "只收" in _rerr, "image/png" in _rerr], [False, True, True])
    chk("G7", "number 归一成 int 并进 param（20 不是 '20'）",
        r["params"].get("duration"), 20)
    chk("G8", "number 越界 ⇒ 拒并说是哪一栏",
        bool((MA.form_submit({"session": "g-live", "skill": "probe-skill",
                              "values": {"duration": "300"}}) or {}).get("error")), True)
    chk("G9", "number 非数字 ⇒ 拒（不悄悄丢掉，悄悄丢=客户填了没生效）",
        bool((MA.form_submit({"session": "g-live", "skill": "probe-skill",
                              "values": {"duration": "十五"}}) or {}).get("error")), True)
    chk("G10", "🔴 多槽时文件没标 field ⇒ 拒（猜=把脸图当产品图塞进引擎）",
        bool((MA.form_submit({"session": "g-live", "skill": "probe-skill",
                              "values": {"duration": "20"}, "files": [PNG]}) or {}).get("error")),
        True)
    chk("G11", "video 的 MIME 进 images 槽 ⇒ 拒（每个槽只收自己那族）",
        bool((MA.form_submit({"session": "g-live", "skill": "probe-skill",
                              "values": {"duration": "20"},
                              "files": [dict(MP4, field="model_image")]}) or {}).get("error")), True)
    chk("G12", "超过字段表写的张数上限 ⇒ 拒（上限只在字段表那一处，渲染器没有第二份）",
        bool((MA.form_submit({"session": "g-live", "skill": "probe-skill",
                              "values": {"duration": "20"},
                              "files": [dict(PNG, field="model_image")] * 2}) or {}).get("error")),
        True)

    # ---- 第 7 列 = 这一槽自己的体积上限（2026-10-09 为 prompt-reverse 的 15MB 硬限加的）----
    #      为什么必须有闸：引擎各条线上限不一样，通用 200MB 让客户选了个【必被引擎拒】的文件
    #      ⇒ 按钮黄了、提交却被拒，等于把一次失败推到引擎去。这一列今天之前【码有、闸零条】。
    def _one(md, k):
        fs = MA.parse_skill_form(md)
        return fs[k] if len(fs) > k else {}
    MD15 = "---\nname: x\ndescription: y\nform: |\n  a|video|A|no|1|p|15MB\n"
    MDBAD = "---\nname: x\ndescription: y\nform: |\n  a|video|A|no|1|p|abc\n"
    chk("G22", "第 7 列解析成字节（探针 1KB、真值 15MB）；坏值不炸也不臆造上限",
        [gk["vid"].get("max_bytes"), _one(MD15, 0).get("max_bytes"), _one(MDBAD, 0).get("max_bytes")],
        [1024, 15728640, None])
    # 🔴 体积夹具必须是【合法 base64】：`"QQ==" * 3000` 看着是 3000 字节，其实把带填充的组串起来了，
    #    b64decode(validate=True) 直接抛 ⇒ 拒是拒了，但拒因是"不是合法 base64"，体积那条根本没跑到
    #    （G23b 那条对照一红就暴露了这件事）。用 b64encode 生成真实字节数，别手拼。
    _big = {"name": "v.mp4", "type": "video/mp4", "data": base64.b64encode(b"x" * 3000).decode()}   # 3000B > 探针那槽 1KB
    _small = {"name": "v.mp4", "type": "video/mp4", "data": base64.b64encode(b"x" * 300).decode()}  # 300B < 1KB
    rb = MA.form_submit({"session": "g-live", "skill": "probe-skill", "values": {"duration": "20"},
                         "files": [dict(_big, field="vid")]})
    chk("G23", "超【这一槽】的上限 ⇒ 拒，并把大小/上限说在错误里（不是悄悄收下再去引擎炸）",
        [bool(rb.get("error")), any(w in str(rb.get("error")) for w in ("KB", "MB", "体积", "上限"))],
        [True, True])
    # 🔴 反向对照：同样 3000B 的字节进【没写第 7 列】的 images 槽 ⇒ 通过。
    #    没有这条，G23 可能只是"提交里带文件就拒"的恒拒（判据看着红得对，其实什么都没判）。
    #    ⚠️ 夹具要换【文件族】也要换对 MIME：上一版把 video/mp4 塞进 images 槽，结果它被"每槽只收自己那族"
    #       拦下（G11 那条规则），拒因又和体积无关 ⇒ 对照必须用同体积的 PNG，否则红的是夹具不是判据。
    _big_png = {"name": "p.png", "type": "image/png", "data": base64.b64encode(b"x" * 3000).decode()}
    rs = MA.form_submit({"session": "g-live", "skill": "probe-skill", "values": {"duration": "20"},
                         "files": [dict(_small, field="vid"), dict(_big_png, field="product_images")]})
    chk("G23b", "同样体积(3000B)进【没写上限的 images 槽】⇒ 通过（证明 G23 判的是那一列，不是恒拒）",
        [bool(rs.get("ok")), rs.get("error") or ""], [True, ""])
    chk("G23c", "这一槽在上限内 ⇒ 通过（上限双向有效，不是把整槽锁死）",
        bool(MA.form_submit({"session": "g-live", "skill": "probe-skill", "values": {"duration": "20"},
                             "files": [dict(_small, field="vid")]}).get("ok")), True)
    wire = MA._form_fields_wire(gf)
    chk("G24", "上限要【下发给页面】（accept/max_bytes 与判据同源，渲染器不抄第二份）",
        [[w.get("max_bytes") for w in wire if w.get("key") == "vid"],
         all(w.get("accept") for w in wire if w.get("type") in ("images", "video"))],
        [[1024], True])
    _pr = [fs for nm, fs in FORM_SKILLS if nm == "prompt-reverse"]
    _prvid = [f for f in (_pr[0] if _pr else []) if f.get("key") == "vid"]
    chk("G25", "🔴 真线核对：prompt-reverse 那一槽确实带 15MB（这一列不是只在探针里活着）",
        [len(_pr), len(_prvid), _prvid[0].get("max_bytes") if _prvid else None], [1, 1, 15728640])
    # 纯中文名洗完是空的 ⇒ 兜底词必须跟着【槽类型】走：视频槽兜成 01-image.mp4 会让人以为传错了文件
    zh = MA.form_submit({"session": "g-live", "skill": "probe-skill", "values": {"duration": "20"},
                         "files": [dict(_small, name="爆款样本.mp4", field="vid"),
                                   dict(PNG, name="产品正面.png", field="model_image")]})
    zp = [str((zh.get("params") or {}).get("video_path") or ""),
          str(((zh.get("params") or {}).get("model_image_path") or [""])[0]
              if isinstance((zh.get("params") or {}).get("model_image_path"), list)
              else (zh.get("params") or {}).get("model_image_path") or "")]
    def _nosh(b):
        # 2026-10-10 起文件名是 `序号-sha8-名.扩展`。这条断言测的是【兜底词跟槽类型走】，
        # 所以先把 sha8 段剥掉再比 —— 直接改期望值会把原意图冲掉，那等于删断言。
        return re.sub(r"^(\d\d)-[0-9a-f]{8}-", r"\1-", os.path.basename(b))
    chk("G26", "中文名洗成 ASCII 后为空 ⇒ 视频槽兜 01-video.mp4、图片槽兜 01-image.png（不再互相拿对方的词）",
        [_nosh(zp[0]), _nosh(zp[1])], ["01-video.mp4", "01-image.png"])
    _sha_v = hashlib.sha256(base64.b64decode(_small["data"])).hexdigest()[:8]
    chk("G26b", "🔴 落盘名 = 序号-sha8-ascii名.扩展，且 sha8 是这份字节【独立算出来】的真哈希",
        os.path.basename(zp[0]), "01-%s-video.mp4" % _sha_v)

    # ---- 回填：用【只登记、从未提交】的 g-pre（已提交过的会话按纪律不再合并 defaults） ----
    pr = MA.form_prefill({"session": "g-pre", "skill": "probe-skill",
                          "values": {"duration": "25", "engine": "gpt 默认", "made_up": "x"},
                          "dropped_note": "从客户上传的 3 张图读到"})
    try:
        _pl = json.load(open(g_payload, encoding="utf-8"))
    except (OSError, ValueError):
        _pl = {}
    chk("G13", "回填写成功，且【不碰载荷】：载荷里只有真提交过的 g-live，没有 g-pre",
        (bool(pr.get("ok")), os.path.exists(g_prefill), "g-live" in _pl, "g-pre" in _pl),
        (True, True, True, False))
    gv = MA.form_gate_view("g-pre")
    chk("G14", "🔴 回填后 submitted 仍 false（agent 永远替客户交不了表）",
        (gv.get("show"), gv.get("submitted")), (True, False))
    chk("G15", "回填的 defaults 进门控答复（渲染器才有东西可预填）",
        gv.get("defaults"), {"duration": "25", "engine": "gpt 默认"})
    chk("G16", "字段表里没有的 key 进 dropped（不静默、不臆造成引擎入参）",
        gv.get("prefill_dropped"), ["made_up"])
    outside = os.path.join(GSB, "outside.png")
    open(outside, "wb").write(b"x")
    chk("G17", "回填的文件路径不在表单上传目录里 ⇒ 拒（这条口不是任意文件读取器）",
        bool((MA.form_prefill({"session": "g-pre", "skill": "probe-skill",
                               "files": [{"field": "product_images", "path": outside}]})
              or {}).get("error")), True)
    # 正向对照要落在【本会话自己的素材根】里：2026-10-10 起守卫从"全局一根"收成"每会话/每任务一根"。
    _pre_slot = os.path.join(MA._form_sid_dir("g-pre"), "product_images")
    os.makedirs(_pre_slot, exist_ok=True)
    p_pre = os.path.join(_pre_slot, "01-%s-mine.png" % hashlib.sha256(b"pre").hexdigest()[:8])
    open(p_pre, "wb").write(b"pre")
    chk("G17b", "正向对照：路径在【本会话】素材根里 ⇒ 回填成功（证明 G17 不是恒拒）",
        bool((MA.form_prefill({"session": "g-pre", "skill": "probe-skill",
                               "files": [{"field": "product_images", "path": p_pre}]})
              or {}).get("ok")), True)
    chk("G17c", "回填的文件按【槽的 param 名】进 default_files（渲染器才知道画哪一格）",
        (MA.form_gate_view("g-pre") or {}).get("default_files"), {"product_image_paths": [p_pre]})
    # 🔴 单槽也必须带数组壳。现网形状（2026-10-10）：`_form_prefill_paths` 对上限=1 的 video 槽
    #    提前拍平成字符串，而页面有一句 `if (!Array.isArray(l)) delete default_files[...]`
    #    ⇒ 原片明明回填成功了，界面上那一格却是空的，客户以为 agent 什么都没做。
    _vslot = os.path.join(MA._form_sid_dir("g-pre"), "vid")
    os.makedirs(_vslot, exist_ok=True)
    p_vid = os.path.join(_vslot, "01-%s-orig.mp4" % hashlib.sha256(b"orig").hexdigest()[:8])
    open(p_vid, "wb").write(b"orig")
    MA.form_prefill({"session": "g-pre", "skill": "probe-skill",
                     "files": [{"field": "vid", "path": p_vid}]})
    _df = (MA.form_gate_view("g-pre") or {}).get("default_files") or {}
    chk("G17f", "🔴 上限=1 的 video 槽回填后仍是【数组】（引擎要的那条路径由提交侧自己取，不在这里变形）",
        [isinstance(_df.get("video_path"), list), _df.get("video_path")], [True, [p_vid]])
    chk("G17d", "🔴 跨会话回填必须被拒：拿【另一条会话】目录里的文件来回填 ⇒ 不吃（旧规矩下这条会成功）",
        bool((MA.form_prefill({"session": "g-pre", "skill": "probe-skill",
                               "files": [{"field": "product_images", "path": p_i}]})
              or {}).get("error")), True)
    _gv = MA.form_gate_view("g-pre")
    chk("G17e", "门控下发 upload_root / task_dir ⇒ 脚本不再自己拼路径（路径规则只有一份）",
        [bool(_gv.get("upload_root")), _gv.get("task_dir", "")], [True, ""])
    # ---- 任务目录就是素材的家（2026-10-10 拍：inputs/src 分开、守卫收到每任务一根）----
    _td = os.path.join(SCRATCH, "task-home")
    for _s in ("inputs", "src"):
        os.makedirs(os.path.join(_td, _s), exist_ok=True)
    _in = os.path.join(_td, "inputs", "01-deadbeef-mine.png")
    _outs = os.path.join(_td, "src", "01-cafebabe-derived.png")
    open(_in, "wb").write(b"i")
    open(_outs, "wb").write(b"o")
    _G["g-pre"]["task_dir"] = _td
    chk("G20", "🔴 登记了 task_dir ⇒ 本会话素材根 = `<task>/inputs`（素材与 BRIEF/outputs 同根）",
        os.path.realpath(MA._form_sid_dir("g-pre")), os.path.realpath(os.path.join(_td, "inputs")))
    chk("G20b", "同任务目录里的文件 ⇒ 认（回填不再要求先复制进 .dsh 隐藏目录）",
        bool((MA.form_prefill({"session": "g-pre", "skill": "probe-skill",
                               "files": [{"field": "product_images", "path": _in}]}) or {}).get("ok")), True)
    _rc = MA.form_prefill({"session": "g-pre", "skill": "probe-skill",
                           "files": [{"field": "product_images", "path": _outs}]})
    chk("G20c", "inputs 与 src 是真的分家：回填 `src/`（agent 派生物）⇒ 拒，且拒因点名目录",
        [bool(_rc.get("error")), "不在表单上传目录里" in str(_rc.get("error")),
         "01-cafebabe-derived.png" in str(_rc.get("error"))], [True, True, True])
    _G["g-pre"]["task_dir"] = r"D:\video-skill"
    chk("G20d", "task_dir 指向工作区外 ⇒ 不认，退回 .dsh/form-uploads/<hash>（不给越界写开口子）",
        os.path.realpath(MA._form_sid_dir("g-pre")).startswith(os.path.realpath(MA.FORM_UPLOAD_DIR)), True)
    _G["g-pre"].pop("task_dir", None)
    _G["g-pre"] = {"skill": "omni", "task": "", "ts": _t.time()}
    chk("G18", "换了技能档 ⇒ 上一条线的回填不再算数（omni 的表不认 product-video 的默认值）",
        (MA.form_gate_view("g-pre") or {}).get("defaults"), None)
    _G["g-pre"] = {"skill": "product-video", "task": "", "ts": _t.time()}
    chk("G18b", "对照：意图改回来 ⇒ 门控重新认这张表（G18 不是因为会话彻底失效而假绿）",
        (MA.form_gate_view("g-pre") or {}).get("skill"), "probe-skill")
    # ---- 对照：submitted 只可能由【真提交】翻 true（G14 不是因为整条链路没跑通而假绿） ----
    chk("G19", "对照：必填文件槽没交 ⇒ 拒（过去只卡文本必填，空提交会一路到引擎才炸）",
        bool((MA.form_submit({"session": "g-live", "skill": "probe-skill",
                              "values": {"duration": "20"}}) or {}).get("error")), True)
    chk("G19b", "对照：交齐必填 ⇒ ok，且 submitted 变 true",
        (bool(MA.form_submit({"session": "g-live", "skill": "probe-skill",
                              "values": {"duration": "20"},
                              "files": [dict(MP4, field="vid")]}).get("ok")),
         bool((MA.form_gate_view("g-live") or {}).get("submitted"))), (True, True))
    # G20：对【被回填过的那条会话】做真提交 ⇒ 门控以载荷为准、defaults 不再回（提交优先于回填）
    MA.form_prefill({"session": "g-pre", "skill": "probe-skill", "values": {"duration": "30"}})
    MA.form_submit({"session": "g-pre", "skill": "probe-skill", "values": {"duration": "40"},
                    "files": [dict(MP4, field="vid")]})
    gv2 = MA.form_gate_view("g-pre") or {}
    chk("G20", "提交过的会话以【载荷】为准：submitted=true、defaults 不再回、params 用提交值",
        (gv2.get("submitted"), "defaults" in gv2, gv2.get("params", {}).get("duration")),
        (True, False, 40))
finally:
    (MA.SKILLS_DIR, MA.FORM_PAYLOAD_PATH, MA.FORM_UPLOAD_DIR, MA.FORM_PREFILL_PATH,
     MA.hypit_bridge.intent_map) = _real_g
    shutil.rmtree(GSB, ignore_errors=True)
chk("G21", "🔴 自测只打在临时目录：真载荷与【真回填文件】的存在与 mtime 一字未动",
    (_probe(_real_g[1]), _probe(MA.FORM_PREFILL_PATH)),
    (PROD_BEFORE, PROD_PREFILL_BEFORE))

# ---------- F 品牌与体验（2026-10-09 产品要求："要有品牌感、不能是简陋表格、要有体验感"） ----------
APPROVAL_SRC = open(os.path.join(GW, "backend", "model_admin.py"), encoding="utf-8",
                    errors="replace").read()
chk("F1", "品牌 token 只有一份：:root 只出现在 WS_TOKENS（深色 1 + 浅色覆盖 1 = 2 段）",
    (PAGE_SRC.count(":root{"), PAGE_SRC.count("--brand:")) , (2, 1))
chk("F2", "🔴 批准页不许再抄一份 :root（两套主色并存就是「简陋感」的来源，必须先收成一处）",
    re.search(r":root\{[^}]*--brand", APPROVAL_SRC) is not None, False)
chk("F3", "页面必须挂品牌识别：logo 与 slogan 都在",
    ("/assets/weeksir-logo.png" in PAGE_JS) and ("专注电商AI落地" in PAGE_SRC), True)
chk("F4", "明暗自适应那条 media query 在（不能只做好看的一半）",
    "prefers-color-scheme:light" in PAGE_SRC, True)
chk("F5", "🔴 媒体槽不是裸 <input type=file>：要有拖拽容器与缩略图渲染",
    ('class:"dz"' in PAGE_JS or "dz" in PAGE_JS) and ("dragover" in PAGE_JS)
    and ("thumbs" in PAGE_JS) and ('type:"file"' in PAGE_JS), True)
# 品牌色只能在 WS_TOKENS 里出现一次；模板正文里再出现一次 = 有人开始就地写死颜色
chk("F6", "模板正文里不许出现任何品牌色/十六进制色值（一律走 var(--x)）",
    [m for m in re.findall(r"#[0-9a-fA-F]{3,6}", PAGE_JS) if m.lower() not in ("#000", "#fff")], [])
chk("F6b", "品牌橙在【代码行】里只定义一次（注释里提它不算第二份）",
    sum(1 for l in PAGE_SRC.splitlines() if "#fa6f08" in l and not l.strip().startswith("#")), 1)
chk("F7", "反向对照：品牌色确实定义在 token 里（F6 不是因为整份没颜色而绿）",
    ("--brand:#fa6f08" in PAGE_SRC) and ("--on-brand" in PAGE_SRC), True)
chk("F8", "🔴 品牌色底上的字必须深色（实测白字 2.87:1 不过 WCAG AA）",
    "color:var(--on-brand)" in PAGE_SRC or "var(--on-brand)" in PAGE_JS, True)
chk("F9", "已提交要转确认态并列出最终参数（客户看得见自己交了什么）",
    "参数已收到" in PAGE_SRC, True)
chk("F10", "一个模板服务全部 skill：页面里不许出现某个技能名的字面量（40 页 = 新泥球）",
    bool(re.search(r"one-shot-video|prompt-reverse|super_clone", PAGE_JS)), False)


# ---- F11 品牌 logo 资源本体（2026-10-09 用户："logo 从官网去找，这样贴太丑了"）----
# 根因不是画错了，是【不透明】：盘上那张 336×192 左上角 = [247,247,248,255]，贴到深色页
# 就是一块白矩形。所以钉【资源本体】而不是钉 CSS —— CSS 改多少回都不保证那张图是透明的。
def _png_head(b):
    """取 (宽, 高, 颜色类型, 左上角 alpha)。
    首行像素不必做 unfilter：PNG 第一行没有上一行、最左没有左像素 ⇒ 五种滤波的预测值都是 0，
    重建值恒等于原始字节。每行开头有 1 个滤波字节，所以左上角 RGBA = px[1:5]。"""
    w, h = struct.unpack(">II", b[16:24])
    ct, i, idat = b[25], 8, b""
    while i + 8 <= len(b):
        ln = struct.unpack(">I", b[i:i + 4])[0]
        if b[i + 4:i + 8] == b"IDAT":
            idat += b[i + 8:i + 8 + ln]
        i += 12 + ln
    px = zlib.decompress(idat)
    return w, h, ct, (px[4] if ct == 6 else 255)


def _logo_ok(w, h, ct, a):
    return ct == 6 and a == 0


LOGO_GW = os.path.join(GW, "backend", "assets", "weeksir-logo.png")
LOGO_PLUG = r"C:\dsh\plugins\super-clone-ui\assets\weeksir-logo.png"
_lg, _lp = open(LOGO_GW, "rb").read(), open(LOGO_PLUG, "rb").read()
chk("F11", "🔴 8100 供的 logo 必须是【透明】那张（RGBA + 左上角 alpha=0），白底图贴深色页就是白块",
    _logo_ok(*_png_head(_lg)), True)
chk("F11b", "反向对照：同一判据遇到 alpha=255 必须 False（绿不是因为压根没读到像素）",
    _logo_ok(*_png_head(_lg)[:3], 255), False)
chk("F11c", "logo 唯一源：8100 那份与插件那份字节相同（否则改一张，另一张白底的原地复活）",
    hashlib.sha256(_lg).hexdigest(), hashlib.sha256(_lp).hexdigest())

# ---------- H 分发值表（钉 2026-10-09 抓到的那条真缺陷：回填曾把载荷也写了） ----------
_calls = []
_real_submit, _real_prefill, _real_attempt = MA.form_submit, MA.form_prefill, MA.form_attempt


def _spy_submit(b):
    _calls.append("submit")
    return {"ok": True, "via": "submit"}


def _spy_prefill(b):
    _calls.append("prefill")
    return {"ok": True, "via": "prefill"}


def _spy_attempt(b):
    _calls.append("attempt")
    return {"ok": True, "via": "attempt"}


try:
    MA.form_submit, MA.form_prefill, MA.form_attempt = _spy_submit, _spy_prefill, _spy_attempt
    # H0 对照：先证明上面那对 spy 抓得住【两个都调】这个缺陷形状 —— 没有这条，
    #     H1 绿只能说明"没抛异常"，说明不了判据有牙（2026-10-09 那条缺陷正是这个形状）。
    _calls[:] = []

    def buggy_dispatch(b):
        r = MA.form_submit(b)
        if str((b or {}).get("stage") or "") == "prefill":
            r = MA.form_prefill(b)
        return r

    buggy_dispatch({"session": "s", "stage": "prefill"})
    chk("H0", "对照：把缺陷形状（先 submit 再 prefill）喂给同一对 spy ⇒ 必须记到两次调用",
        _calls, ["submit", "prefill"])
    _calls[:] = []
    r1 = MA.form_route_dispatch({"session": "s", "skill": "x", "stage": "prefill", "values": {}})
    chk("H1", "🔴 stage=prefill ⇒ 【只】调 form_prefill，一次都不调 form_submit（不替客户交表）",
        (_calls, r1.get("via")), (["prefill"], "prefill"))
    _calls[:] = []
    MA.form_route_dispatch({"session": "s", "skill": "x", "values": {}})
    chk("H2", "没有 stage ⇒ 只调 form_submit 一次（提交口不被回填绕开）", _calls, ["submit"])
    _calls[:] = []
    MA.form_route_dispatch({"session": "s", "skill": "x", "stage": "PREFILL ", "values": {}})
    chk("H3", "stage 大小写/空格不影响判档（不是靠字符串恰好相等）", _calls, ["prefill"])
    _calls[:] = []
    MA.form_route_dispatch({"session": "s", "stage": "prefill", "values": {}})   # 缺 skill 也要走回填档
    chk("H4", "分发不看 skill（缺 skill 时由下游拒，不在这里改道成提交）", _calls, ["prefill"])
    _calls[:] = []
    MA.form_route_dispatch(None)
    chk("H5", "传进来不是字典也不炸（闸自己不许成为故障点）", _calls, ["submit"])
    _calls[:] = []
    MA.form_route_dispatch({"session": "s", "skill": "x", "stage": "attempt", "missing": ["原片"]})
    chk("H6", "🔴 stage=attempt ⇒ 【只】调 form_attempt 一次，不碰 submit/prefill"
        "（「点一下提交」就伪造出「客户已授权开工」，是 prefill≠submitted 这条红线最怕的形状）",
        _calls, ["attempt"])
    _calls[:] = []
    MA.form_route_dispatch({"session": "s", "stage": " ATTEMPT "})
    chk("H6b", "attempt 档与 prefill 同一套归一判档（大小写/空格不影响，不各写一份规则）", _calls, ["attempt"])
finally:
    MA.form_submit, MA.form_prefill, MA.form_attempt = _real_submit, _real_prefill, _real_attempt

# ---------- I9 提交尝试台账：查「我点了没反应」的唯一现场 ----------
# 真跑服务端函数（不是 grep），并且钉死两件事：① 它【不写】载荷/回填 ⇒ 点了没成交永远不算授权；
# ② 环形上限 ⇒ 诊断面不许长成盘。
MA_SRC = open(os.path.join(GW, "backend", "model_admin.py"), encoding="utf-8", errors="replace").read()
_ATP = os.path.join(SCRATCH, "attempts-sb")
shutil.rmtree(_ATP, ignore_errors=True)
os.makedirs(_ATP, exist_ok=True)
_real_paths = (MA.FORM_ATTEMPT_PATH, MA.FORM_PAYLOAD_PATH, MA.FORM_PREFILL_PATH)
MA.FORM_ATTEMPT_PATH = os.path.join(_ATP, "attempts.json")
MA.FORM_PAYLOAD_PATH = os.path.join(_ATP, "form_payload.json")
MA.FORM_PREFILL_PATH = os.path.join(_ATP, "form_prefill.json")
try:
    r0 = MA.form_attempt({"session": "", "missing": ["原片"]})
    chk("I9a", "没有会话 id 的 attempt ⇒ 拒（台账不是匿名垃圾场，对不上号就等于没记）",
        [bool(r0.get("error")), os.path.exists(MA.FORM_ATTEMPT_PATH)], [True, False])
    for _i in range(25):
        MA.form_attempt({"session": "session-probe", "skill": "super-clone", "task": "t%d" % _i,
                         "missing": ["原片", "声音替换"], "reason": "前端判缺项，未发提交"})
    _rows = MA.form_attempts_view(50).get("recent") or []
    chk("I9b", "连点 25 次 ⇒ 台账只留 20 条（环形上限），且按时间倒序给",
        [len(_rows), _rows[0].get("task"), _rows[0].get("missing")], [20, "t24", ["原片", "声音替换"]])
    chk("I9c", "🔴 记了台账 ⇒ 载荷文件与回填文件【都没被创建】（点了但没成交 ≠ 客户授权开工）",
        [os.path.exists(MA.FORM_PAYLOAD_PATH), os.path.exists(MA.FORM_PREFILL_PATH)], [False, False])
    chk("I9d", "读取口只听环回（台账里带会话 id 与任务名，局域网里别的机器不该看见）",
        '_not_loopback("/api/skills/form/attempts")' in MA_SRC, True)
finally:
    MA.FORM_ATTEMPT_PATH, MA.FORM_PAYLOAD_PATH, MA.FORM_PREFILL_PATH = _real_paths
    shutil.rmtree(_ATP, ignore_errors=True)
chk("I9e", "页头必须带会话尾号：多会话并发 + 任务名可重名，「我点的到底是哪条会话的表」要一眼能认",
    "会话 " in PAGE_CODE and 'getElementById("skill")' in PAGE_CODE, True)
chk("I9f", "🔴 reportAttempt 三个现场都要调得到：前端拦 / 服务端拒 / fetch 没送到"
    "（漏一个就还是「查不到现场」，而这次那条会话正是被误判成「你提交到了别的会话」的）",
    [PAGE_CODE.count("reportAttempt("), PAGE_CODE.count("reportAttempt(miss.map"),
     PAGE_CODE.count('reportAttempt([], (j && j.error)'), PAGE_CODE.count('reportAttempt([], "fetch')],
    [4, 1, 1, 1])
chk("I9g", "反向对照：台账真函数确实会写文件（I9c 的 False 不是因为整个口坏了）",
    [bool(MA.form_attempt({"session": "session-probe2", "reason": "x"}).get("ok")),
     os.path.getsize(MA.FORM_ATTEMPT_PATH) > 0], [True, True])

# 🔴 覆盖对照：这条台账【真的接在提交口上】——路由里确实调了 form_route_dispatch，
#    少这一句的话，上面所有值表都是在测一个没人调用的函数（"闸全绿、线上没接线"就是这么来的）。
chk("I9h", "🔴 提交真口确实走 form_route_dispatch（attempt 档不是住在没人调的地方）",
    [MA_SRC.count("= form_route_dispatch("), MA_SRC.count("def form_route_dispatch(")], [1, 1])

n_ok = sum(1 for _, ok in RESULTS if ok)
cprint("\n=== 技能表单闸汇总：%d/%d 通过 ===" % (n_ok, len(RESULTS)))
for cid, ok in RESULTS:
    if not ok:
        cprint("   未通过：%s" % cid)
sys.exit(0 if n_ok == len(RESULTS) else 1)

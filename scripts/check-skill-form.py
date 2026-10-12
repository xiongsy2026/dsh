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
# 🔴 钉的是规则不是字面：2026-10-11 入口多了 --from-form 那条分支，它的第一次 read_form_gate 在
#    "读不到会话 id" 那句之前 —— 文案顺序变了、规则没变（没 id 就拒、绝不猜）。所以两条拒因任一在前都算，
#    但"绝不猜一个填进去"这句必须在（那是这条规则的本体）。
chk("D9e", "读不到会话 id ⇒ 明说不判/直接拒（绝不拿空 id 去查、也绝不猜一个填进去）",
    ([("读不到会话 id" in _m[:_rg]) or ("要会话 id" in _m[:_rg]),
      "绝不猜一个填进去" in _m[:_rg]] if _rg >= 0 else [False, False]), [True, True])
_bad2 = "sid = a.session\nread_form_gate(sid)"
chk("D9e2", "反向对照：D9e 不是恒真 —— 拿一段「没 id 就直接查表」的违规样例喂同一对探针 ⇒ 两条都必须不成立",
    [any(p in _bad2 for p in ("读不到会话 id", "要会话 id")), "绝不猜一个填进去" in _bad2], [False, False])

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
    # ---- G29 条件必填的【媒体槽】不许卡隐藏格（2026-10-10 现网：super-clone 在"原声保留"下
    #      参考音频是隐藏格，客户连点 8 次提交全被「必填没交：参考音频」拒 ⇒ agent 误判成"没配档"）----
    os.makedirs(os.path.join(GSB, "cond-skill"), exist_ok=True)
    open(os.path.join(GSB, "cond-skill", "SKILL.md"), "w", encoding="utf-8").write(
        "---\nname: cond-video\nintent: cond-video\nform: |\n"
        "  mode|select|模式|yes|keep 保留原声;swap 换音色|mode\n"
        "  voice|audio|参考音频|yes|1|voice_paths|show=mode=swap\n"
        "  vid|video|源|yes|1|video_path\n---\n正文\n")
    _G["g-cond"] = {"skill": "cond-video", "task": "", "ts": _t.time()}
    ok_hide = MA.form_submit({"session": "g-cond", "skill": "cond-skill",
                              "values": {"mode": "keep 保留原声"},
                              "files": [dict(MP4, field="vid")]})
    chk("G29", "🔴 隐藏的必填音频槽【不卡提交】：mode=保留原声 + 不交音频 ⇒ 应 ok",
        [bool(ok_hide.get("ok")), "参考音频" in str(ok_hide.get("error") or "")], [True, False])
    need = MA.form_submit({"session": "g-cond", "skill": "cond-skill",
                           "values": {"mode": "swap 换音色"},
                           "files": [dict(MP4, field="vid")]})
    chk("G29b", "反向对照：同一格在 mode=换音色（显示中）时缺文件 ⇒ 必须照样拒（证明 G29 不是放水）",
        [bool(need.get("ok")), "参考音频" in str(need.get("error") or "")], [False, True])
    chk("G29c", "拒因要点名是谁：错误串里带那一格的标签",
        "参考音频" in str(need.get("error") or ""), True)
finally:
    (MA.SKILLS_DIR, MA.FORM_PAYLOAD_PATH, MA.FORM_UPLOAD_DIR, MA.FORM_PREFILL_PATH,
     MA.hypit_bridge.intent_map) = _real_g
    shutil.rmtree(GSB, ignore_errors=True)
chk("G21", "🔴 自测只打在临时目录：真载荷与【真回填文件】的存在与 mtime 一字未动",
    (_probe(_real_g[1]), _probe(MA.FORM_PREFILL_PATH)),
    (PROD_BEFORE, PROD_PREFILL_BEFORE))

# ---- G29d/G29e 拿【真表】再跑一遍（2026-10-10 现网事故的正主）----
# G29 那组是临时造的 cond-skill ⇒ 它只证明"机制对"，证明不了"这条线的字段表配了、服务端也认了"。
# 现网坏法正是这个缺口：服务端只在【文本段】用了隐藏判据，【媒体段】漏了 ⇒ super-clone 在
# "原声保留"下参考音频是隐藏格，前端把格藏好、按钮点亮，提交却被「必填没交：参考音频」拒。
# 所以这两条钉的是真 SKILL.md：删掉那一行的 show= 尾巴，G29d 立刻红。
RT = os.path.join(SCRATCH, "real-table")
shutil.rmtree(RT, ignore_errors=True)
os.makedirs(RT)
_real_rt = (MA.FORM_PAYLOAD_PATH, MA.FORM_UPLOAD_DIR, MA.hypit_bridge.intent_map)
try:
    MA.FORM_PAYLOAD_PATH = os.path.join(RT, "form_payload.json")
    MA.FORM_UPLOAD_DIR = os.path.join(RT, "uploads")
    MA.hypit_bridge.intent_map = lambda: {
        "g-real": {"skill": "super-clone", "task": "", "ts": _t.time()}}
    _vals = {"product_name": "黄色开衫", "customer_request": "换成我的产品，锁死运镜",
             "aspect_ratio": "adaptive 自适应（跟原片）"}
    r_keep = MA.form_submit({"session": "g-real", "skill": "super-clone",
                             "values": dict(_vals, voice_mode="preserve_original 原声保留"),
                             "files": [dict(MP4, field="video")]})
    _pk = r_keep.get("params") or {}
    chk("G29d", "🔴 真表 super-clone：原声保留 + 不交参考音频 ⇒ 提交必须过，且两张隐藏格不进载荷",
        [bool(r_keep.get("ok")), "voice_audio" in _pk, "voice_script" in _pk,
         _pk.get("video") is not None], [True, False, False, True])
    r_swap = MA.form_submit({"session": "g-real", "skill": "super-clone",
                             "values": dict(_vals, voice_mode="timbre_swap 只换音色"),
                             "files": [dict(MP4, field="video")]})
    chk("G29e", "反向对照：同一张真表换成「只换音色」⇒ 参考音频回到可见必填，不交必须照样拒",
        [bool(r_swap.get("ok")), "参考音频" in str(r_swap.get("error") or "")], [False, True])
finally:
    (MA.FORM_PAYLOAD_PATH, MA.FORM_UPLOAD_DIR, MA.hypit_bridge.intent_map) = _real_rt
    shutil.rmtree(RT, ignore_errors=True)
chk("G29f", "🔴 真表自测同样只打在临时目录：客户那份真载荷一字未动",
    _probe(_real_rt[0]), PROD_BEFORE)

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
# 🔴 反向对照要【自带沙盒】：这条曾经放在 finally 之后跑，把 session-probe2 写进了线上
#    .dsh/form_attempts.json —— 自测后门只对临时账本有效这条规矩，我自己违反过一次（2026-10-10）。
_ATP2 = os.path.join(SCRATCH, "attempts-sb2")
shutil.rmtree(_ATP2, ignore_errors=True)
os.makedirs(_ATP2, exist_ok=True)
_AT_SAVE = MA.FORM_ATTEMPT_PATH
MA.FORM_ATTEMPT_PATH = os.path.join(_ATP2, "attempts.json")
try:
    chk("I9g", "反向对照：台账真函数确实会写文件（I9c 的 False 不是因为整个口坏了）",
        [bool(MA.form_attempt({"session": "session-probe2", "reason": "x"}).get("ok")),
         os.path.isfile(MA.FORM_ATTEMPT_PATH)], [True, True])
finally:
    MA.FORM_ATTEMPT_PATH = _AT_SAVE
    shutil.rmtree(_ATP2, ignore_errors=True)
def _probe_has(needle, path):
    """线上文件里有没有这条自测会话（读不到文件 = 没有，合法）。"""
    try:
        return needle in open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return False


chk("I9i", "🔴 自测只碰沙盒：线上台账/载荷/回填三份文件里都不许出现 probe 会话",
    [_probe_has("session-probe", MA.FORM_ATTEMPT_PATH), _probe_has("session-probe", MA.FORM_PAYLOAD_PATH),
     _probe_has("session-probe", MA.FORM_PREFILL_PATH)], [False, False, False])

# 🔴 覆盖对照：这条台账【真的接在提交口上】——路由里确实调了 form_route_dispatch，
#    少这一句的话，上面所有值表都是在测一个没人调用的函数（"闸全绿、线上没接线"就是这么来的）。
chk("I9h", "🔴 提交真口确实走 form_route_dispatch（attempt 档不是住在没人调的地方）",
    [MA_SRC.count("= form_route_dispatch("), MA_SRC.count("def form_route_dispatch(")], [1, 1])

# ---- D10~D15 回填覆盖率：料在手却没挂进格子 ⇒ 入口拒（2026-10-10 ef1ba285 现网形状）----
# 事故形状：agent 把原片下载进 <task>/src，<task>/inputs 空着，只回填了 voice_mode 一格
# ⇒ 右栏那张表上"原片"永远空 = 把归类活推回客户。这条判据绑在必然发生的动作（走入口出片）上。
_COV = os.path.join(SCRATCH, "cov-task")
shutil.rmtree(_COV, ignore_errors=True)
for _d in ("inputs", os.path.join("inputs", "video"), "src", "frames", "outputs"):
    os.makedirs(os.path.join(_COV, _d), exist_ok=True)
_COV_VID = os.path.join(_COV, "src", "original.mp4")
open(_COV_VID, "wb").write(b"fake-video-bytes-for-slot-typing")
open(os.path.join(_COV, "frames", "f_01.jpg"), "wb").write(b"fake-frame")   # 派生物：绝不能被当素材
_COV_ROOTS = [os.path.join(_COV, "inputs"), os.path.join(_COV, "src")]
_CF = MA.skill_form_index()["super-clone"]["fields"]

chk("D10", "🔴 必填格没值的判定：隐藏格（show= 不命中）不卡必填、locked 不参与，素材格看 media "
    "（voice_audio 只在换声那两档才算缺 —— 拿它卡死会把人永久锁在入口外）",
    [sorted(MA.form_unfilled_required(_CF, {}, {})),
     sorted(MA.form_unfilled_required(_CF, {"voice_mode": "preserve_original"}, {})),
     sorted(MA.form_unfilled_required(_CF, {"voice_mode": "timbre_swap"}, {})),
     "engine_lock" in str(MA.form_unfilled_required(_CF, {}, {}))],
    [["aspect_ratio", "customer_request", "product_name", "video", "voice_mode"],
     ["aspect_ratio", "customer_request", "product_name", "video"],
     ["aspect_ratio", "customer_request", "product_name", "video", "voice_audio"],
     False])

chk("D11", "🔴 素材格有没有值看的是【引擎参数名 param】那份 media：回填按 param 落盘，"
    "按 key 查就永远算没填（假拦 = 入口把已经填好的表拒之门外）",
    [sorted(MA.form_unfilled_required(_CF, {"voice_mode": "preserve_original"}, {"video": [_COV_VID]})),
     sorted(MA.form_unfilled_required(_CF, {"voice_mode": "preserve_original"},
                                      {"video": []}))],
    [["aspect_ratio", "customer_request", "product_name"],
     ["aspect_ratio", "customer_request", "product_name", "video"]])

chk("D12", "🔴 覆盖率判据值表：原片躺在 src/ 却没引用 ⇒ gap=['video']；挂上去 ⇒ 清零。"
    " src/ 必须一起扫（只扫 inputs/ 抓不到这次事故）；frames/ 与 outputs/ 不许扫（派生物会天天误拦）",
    [MA.form_prefill_gap(_CF, {}, {}, _COV_ROOTS),
     MA.form_prefill_gap(_CF, {}, {"video": [_COV_VID]}, _COV_ROOTS),
     [os.path.basename(p) for p in MA.form_loose_materials(_COV_ROOTS, {})],
     MA.form_prefill_gap(_CF, {}, {}, [os.path.join(_COV, "frames"), os.path.join(_COV, "outputs")]),
     MA.form_prefill_gap(_CF, {"voice_mode": "preserve_original"}, {}, _COV_ROOTS)],
    [["video"], [], ["original.mp4"], [], ["video"]])

chk("D12b", "反向对照·按类型配槽：根里只有视频时，缺【图片】槽不判缺（否则图片槽一空就永远出不去）",
    [sorted(MA.form_slot_material_candidates(_CF, {"voice_mode": "preserve_original"},
                                             {}, _COV_ROOTS)),
     "product_images" in MA.form_slot_material_candidates(
         _CF, {"voice_mode": "preserve_original"}, {}, _COV_ROOTS),
     MA.form_prefill_gap(_CF, {"voice_mode": "preserve_original"}, {},
                         [os.path.join(_COV, "inputs"), os.path.join(_COV, "src")])],
    [["video"], False, ["video"]])

# 真跑 form_gate_view（自造一条会话：意图/回填/载荷三份都指到沙盒，绝不碰线上文件）
_COV_SID = "session-cov"
_OLD_INTENT_MAP = MA.hypit_bridge.intent_map
_COV_PREFILL = os.path.join(SCRATCH, "cov_prefill.json")
_COV_PAYLOAD = os.path.join(SCRATCH, "cov_payload.json")
try:
    _PV_SAVE, _PL_SAVE = MA.FORM_PREFILL_PATH, MA.FORM_PAYLOAD_PATH
    MA.hypit_bridge.intent_map = lambda: {
        _COV_SID: {"skill": "super-clone", "task": "cov", "ts": _t.time(), "task_dir": _COV}}
    MA.FORM_PREFILL_PATH, MA.FORM_PAYLOAD_PATH = _COV_PREFILL, _COV_PAYLOAD
    open(_COV_PAYLOAD, "w", encoding="utf-8").write("{}")

    def _cov_view(defaults, media):
        json.dump({_COV_SID: {"skill": "super-clone", "intent": "super-clone",
                              "defaults": defaults, "media": media, "ts": _t.time(),
                              "notes": {}, "dropped": []}},
                  open(_COV_PREFILL, "w", encoding="utf-8"))
        return MA.form_gate_view(_COV_SID)

    _v1 = _cov_view({"voice_mode": "preserve_original"}, {})          # 事故形状
    _v2 = _cov_view({"voice_mode": "preserve_original"},
                    {"video": [_COV_VID]})                            # 已归位并回填
    chk("D12c", "🔴 门控一次回齐三份原料（unfilled_required / loose_materials / prefill_gap）"
        "——runner 与 intent.py 都只读结论，判据只在这一份",
        [all(k in _v1 for k in ("unfilled_required", "loose_materials", "prefill_gap")),
         _v1.get("upload_root") == os.path.join(_COV, "inputs"), _v1.get("prefill_gap"),
         _v2.get("prefill_gap"), _v2.get("loose_materials")],
        [True, True, ["video"], [], []])

    chk("D13", "🔴 入口真的据这条拒：已回填但覆盖率不合格 ⇒ 拒；填齐 ⇒ 放行；已提交 ⇒ 不再拦"
        "（客户交完表 agent 必须能接着跑，拦在这儿就是把活卡死）",
    [bool(_fg(dict(_v1, show=True))), bool(_fg(dict(_v2, show=True))),
     bool(_fg(dict(_v1, show=True, submitted=True)))], [True, False, False])

    chk("D13b", "拒绝文案带得出下一步（点名缺哪格、躺着哪个文件、命令长什么样）",
        ["video" in _fg(dict(_v1, show=True)), "original.mp4" in _fg(dict(_v1, show=True)),
         "intent.py prefill" in _fg(dict(_v1, show=True)),
         "inputs" in _fg(dict(_v1, show=True))], [True, True, True, True])

    # 变异对照：把判据整块摘掉 ⇒ 同一条门控答复必须从"拒"翻成"放行"，否则 D13 是空跑
    _gap_save, _cg_save = MA.form_prefill_gap, _sc_ns["form_coverage_gap"]
    try:
        MA.form_prefill_gap = lambda *a, **k: []
        _vm = _cov_view({"voice_mode": "preserve_original"}, {})
        chk("D13m", "变异对照·服务端：form_prefill_gap 恒回空 ⇒ 门控的 prefill_gap 也空（证明这条真被调用）",
            [_vm.get("prefill_gap"), _v1.get("prefill_gap")], [[], ["video"]])
        _sc_ns["form_coverage_gap"] = lambda g: ""
        chk("D13n", "🔴 变异对照·入口：把 form_coverage_gap 摘成恒放行 ⇒ 同一份答复不再拒"
            "（这条不翻红就说明入口根本没在用那个结论）",
            bool(_fg(dict(_v1, show=True))), False)
    finally:
        MA.form_prefill_gap, _sc_ns["form_coverage_gap"] = _gap_save, _cg_save
finally:
    MA.hypit_bridge.intent_map = _OLD_INTENT_MAP
    MA.FORM_PREFILL_PATH, MA.FORM_PAYLOAD_PATH = _PV_SAVE, _PL_SAVE

# 判据不许有第二份：入口只读 prefill_gap，自己再解析一遍字段表就是漂的开始
_cg_src = SC_RUN[SC_RUN.index("def form_coverage_gap"):SC_RUN.index("def form_gate_verdict")]
chk("D14", "🔴 覆盖率结论只有一份：入口函数体里读 prefill_gap，且【不出现】fields/accept/type 的自解析",
    ["prefill_gap" in _cg_src, any(t in _cg_src for t in ('get("fields")', "accept", '"type"'))],
    [True, False])
chk("D14b", "反向对照：D14 那颗「自解析」探针本身有效 —— 喂一段真含字段表解析的源码，它必须判【含有】"
    "（探针恒 False 的话 D14 就是空跑全绿）",
    [any(t in '_f.get("fields") + f["type"] + f["accept"]' for t in ('get("fields")', "accept", '"type"'))],
    [True])
_Isrc = open(os.path.join(HERE, "intent.py"), encoding="utf-8").read()
chk("D15", "intent.py 回填完会把服务端那份覆盖率结论【原样打出来】，缺口非空时 next 换成「补齐再来」"
    "（不靠 agent 自己想到「我是不是漏了一格」）",
    [n in _Isrc for n in ('"prefill_gap"', '"loose_materials"', '"unfilled_required"', "if _gap:")],
    [True, True, True, True])
_m15 = _Isrc.replace("if _gap:", "if False:")
chk("D15b", "反向对照：把「缺口非空」那条分支改成永不触发 ⇒ D15 的四条立刻不齐（证明它钉的是真分支，不是恒真）",
    [[n in _m15 for n in ('"prefill_gap"', '"loose_materials"', '"unfilled_required"', "if _gap:")]
     == [True, True, True, True]], [False])

# ── 已提交态能不能改（2026-10-11 现网：客户 08:27 交了表，之后整页被换成只读摘要卡，
#    卡片上那句「要改就回这一栏重新提交一次」指的路被 showDone 自己 innerHTML="" 清掉了 ⇒
#    「好像 ai 帮我选了的我没法选」。钉的是「承诺过的路径必须真存在」，不是某个文案字符串。──
chk("D16", "摘要卡给【改参数】真入口：按钮 + 置 editing + 重新 render（不是一句没有出口的文案）",
    [('"改参数' in PAGE_JS), ("editing = true" in PAGE_JS), ("render(); paint()" in PAGE_JS)],
    [True, True, True])
chk("D17", "摘要卡印的是人话：select 走 matchOption→optLabel，旧的「直接 String(v)」那行必须已消失",
    [("function displayValue" in PAGE_JS), ("matchOption(f, v)" in PAGE_JS),
     ('text:Array.isArray(v) ? (v.length + " 个文件") : String(v)' in PAGE_JS)],
    [True, True, False])
chk("D18", "轮询不许把正在编辑的表盖回只读卡（submitted 前先问 editing / userDirty）",
    ["j.submitted && !(editing || userDirty())" in PAGE_JS], [True])
chk("D19", "editing 有进有出：提交成功与换技能两处都复位（漏一处=这条会话永远不再显示已提交态）",
    [PAGE_JS.count("editing = false"), PAGE_JS.count("editing = true")], [3, 1])
_m16 = re.search(r"onclick:function\(\)\{\s*\n?\s*editing = true;[\s\S]{0,400}?\}\}\);", PAGE_JS)
chk("D20", "反向对照：把【改参数】回调里的 render() 摘掉 ⇒ D16 立刻不齐（证明钉的是真接线）",
    [bool(_m16), "render()" in (_m16.group(0) if _m16 else "")], [True, True])

# ── 2026-10-11 改判：弹表不再等回填 ⇒ 空表必须自己说清"agent 还没看过素材" ──
chk("D21", "表登记完就弹（判定口不再拿回填当前置），页面在未回填时出一行说明，不静默装成正常表",
    [("await-prefill" in PAGE_SRC),
     ("!j.prefilled && !j.submitted" in PAGE_JS),
     ("还没分析这批素材" in PAGE_JS)], [False, True, True])
chk("D22", "反向对照·压力只是换位置：回填仍是 approve 的硬前置（判据在 gateway，页面/判定口都不放行花钱）",
    [("form_prefill_blockers" in open(os.path.join(GW, "backend", "gateway.py"),
                                      encoding="utf-8", errors="replace").read())], [True])

# ── 2026-10-11 现网（同一条会话 20:56 回填 / 21:02 客户重传）：agent 猜字段名，猜错还是【静默】的 ──
# 事故形状：它发的是 source_video / ratio（那是 motion-transfer 那条线的叫法，跨线串味），
# 又把 product_images 当文字塞进 values（媒体格只认 files，服务端对媒体字段直接 continue，连 dropped 都不进）。
# 结果退出码 0、agent 以为填好了 ⇒ 客户第二天把已经在手的视频和图片再传一遍。
# 修法两条：① key 用错 ⇒ 过不去并列出合法 key + files 模板；② intent.py fields 让它不必猜。
import ast  # noqa: E402
_i_tree = ast.parse(_Isrc)
_i_ns = {"os": os}
for _fn in ("prefill_key_problems", "fields_view"):
    _node = next((n for n in _i_tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == _fn), None)
    chk("D23x", "intent.py 里 %s 必须是【模块级函数】（写在 cmd_* 里面就抠不出来跑值表，只能靠读代码信它）"
        % _fn, bool(_node), True)
    if _node:
        exec(compile(ast.get_source_segment(_Isrc, _node), "intent.py:" + _fn, "exec"), _i_ns)
_pk, _fv = _i_ns.get("prefill_key_problems"), _i_ns.get("fields_view")
# 字段表【用服务端下发给门控的那一份】（_form_fields_wire），不在闸里按 type 自己判媒体格 ——
# 否则 intent.py 读 accept、闸读 type，两边各判一套，这条测试就成了自证。
_WIRE = MA._form_fields_wire(SC_FIELDS)
_ALLK = {f["key"] for f in _WIRE}
_MEDIASK = {f["key"] for f in _WIRE if f.get("accept")}
chk("D23y", "覆盖对照：媒体格名单【非空】且含这次真出事的 product_images（空名单会让下面三档全成假绿）",
    [len(_MEDIASK) >= 2, "product_images" in _MEDIASK,
     sorted(_MEDIASK) == sorted({f["key"] for f in _WIRE if f["type"] in MEDIA_TYPES})],
    [True, True, True])
_legal = {k: "x" for k in sorted(_ALLK - _MEDIASK)}
chk("D23", "回填 key 判据值表：正常填 ⇒ 放过；猜错名 ⇒ 1 条；媒体塞 values ⇒ 1 条；两种一起 ⇒ 2 条"
    "（拿真字段表跑，不拿样例跑）",
    [len(_pk(_legal, _ALLK, _MEDIASK)),
     len(_pk({"ratio": "16:9", "source_video": "c:\\a.mp4"}, _ALLK, _MEDIASK)),
     len(_pk({"product_images": "c:\\p.jpg"}, _ALLK, _MEDIASK)),
     len(_pk({"ratio": "16:9", "product_images": "c:\\p.jpg"}, _ALLK, _MEDIASK))],
    [0, 1, 1, 2])
_p1 = _pk({"ratio": "16:9", "source_video": "c:\\a.mp4"}, _ALLK, _MEDIASK)
_p2 = _pk({"product_images": "c:\\p.jpg"}, _ALLK, _MEDIASK)
chk("D23b", "🔴 拒得不许只说「不对」：必须当场给出可抄的东西 —— 全部合法 key、媒体格名单、files 写法"
    "（agent 拿到就能改对，不用再来一轮）",
    [all(k in _p1[0] for k in _ALLK), "媒体格" in _p1[0] and "video" in _p1[0],
     '"files":[{"field":"product_images"' in _p2[0]], [True, True, True])
_cmdp = _Isrc[_Isrc.index("def cmd_prefill"):_Isrc.index("def fields_view")]
chk("D23c", "免猜命令的出口要落在【拒绝那一句】里：die 文案既点名「一条都没提交」，又指到 intent.py fields",
    ["intent.py fields" in _cmdp, "一条都没提交" in _cmdp, '"dropped_keys"' in _cmdp], [True, True, True])
_pk_mut = _Isrc[_Isrc.index("def prefill_key_problems"):_Isrc.index("def cmd_prefill")].replace(
    "if str(k) not in keys", "if False")
_ns_m = {"os": os}
exec(compile(_pk_mut, "intent.py:mutated", "exec"), _ns_m)
chk("D23m", "变异对照：把「未知 key」那句判断摘成恒不成立 ⇒ 猜错名不再被拦（这条必须翻，否则 D23 是空跑）",
    [len(_ns_m["prefill_key_problems"]({"ratio": "16:9"}, _ALLK, _MEDIASK)),
     len(_ns_m["prefill_key_problems"]({"product_images": "p"}, _ALLK, _MEDIASK))], [0, 1])
chk("D24", "🔴 这道拒必须【在所有副作用之前】：key 判据先于复制素材、先于写槽目录、先于 POST 给 8100"
    "（在后面 die = 素材已经复制进 inputs/ 了，只是没登记，盘上留一份没人认领的文件）",
    [(_a := _cmdp.index("prefill_key_problems")) < _cmdp.index("shutil.copyfile"),
     _a < _cmdp.index("urllib.request.Request(FORM_API_POST"),
     _a < _cmdp.index('for n, item in enumerate(body.get("files")'),
     "die(" in _cmdp[_a:_cmdp.index("updir = str(gate")]],
    [True, True, True, True])
_vw = _fv({"upload_root": r"C:\dsh\productions\t\inputs", "fields": _WIRE})
_tv, _tf = _vw["template"]["values"], _vw["template"]["files"]
_sel = [f for f in _WIRE if f["type"] == "select"]
_locks = [f for f in _WIRE if f["type"] == "locked"]
_cond = [f for f in _WIRE if f.get("show_if")]
chk("D25", "🔴 fields 模板必须【按槽形」出：媒体格只进 files（进 values 就是这次事故）、🔒 行两都不进、"
    "select 给的是字段表里的真选项、条件格带「仅当」、说明覆盖每个可填格",
    [sorted(x["field"] for x in _tf) == sorted(_MEDIASK),
     not (_tv.keys() & _MEDIASK), not (_tv.keys() & {f["key"] for f in _locks}),
     all(str(_tv[f["key"]]).split(" ")[0] in [o.split(" ")[0] for o in f["options"]] for f in _sel),
     all(any(f["key"] in ln and "仅当" in ln for ln in _vw["fields"]) for f in _cond),
     set(_vw["template"]["notes"]) == set(_tv.keys()) | _MEDIASK],
    [True, True, True, True, True, True])
_fv_mut = _Isrc[_Isrc.index("def fields_view"):].replace('elif ft == "locked":', "elif False:").replace(
    'if f.get("accept"):', 'if False:').replace("tpl_f.append", "pass #")
_ns2 = {"os": os}
exec(compile(_fv_mut[:_fv_mut.index("\n\n\ndef cmd_fields")], "intent.py:mutated", "exec"), _ns2)
_vm2 = _ns2["fields_view"]({"upload_root": "X", "fields": _WIRE})
chk("D25b", "变异对照：摘掉「媒体走 files」和「🔒 不进模板」两处 ⇒ 同一份字段表产出的模板立刻变形"
    "（证明 D25 钉的是这两个分支，不是恒真断言）",
    [len(_vm2["template"]["files"]) < len(_tf),
     bool(_vm2["template"]["values"].keys() & _MEDIASK),
     bool(_vm2["template"]["values"].keys() & {f["key"] for f in _locks})],
    [True, True, True])
# 🔴 探针必须打在【标识符】上，不是打在整份源码字符串上：intent.py 的注释和 die 文案里就写着
#    "SKILL.md 的 form: 段"，拿整份文本做 not in 会永远红（或被人写进注释里喂绿）。
_i_names = {n.attr for n in ast.walk(_i_tree) if isinstance(n, ast.Attribute)} | \
           {n.id for n in ast.walk(_i_tree) if isinstance(n, ast.Name)}
_i_calls = sum(1 for n in ast.walk(_i_tree)
               if isinstance(n, ast.Call) and "urlopen" in ast.dump(n.func))
chk("D26", "🔴 intent.py 里不许有第二份字段表：它【不调】parse_skill_form，字段表一律现读门控"
    "（唯一源仍是该 skill 的 SKILL.md form: 段）",
    ["parse_skill_form" not in _i_names, _i_calls >= 2], [True, True])
_bad_names = {n.attr for n in ast.walk(ast.parse("x = MA.parse_skill_form(md)"))
              if isinstance(n, ast.Attribute)} | \
             {n.id for n in ast.walk(ast.parse("urllib.request.urlopen(u)")) if isinstance(n, ast.Name)}
chk("D26b", "反向对照：D26 两条探针都能翻红 —— 违规样例（自己解析字段表 / 只有一处现读门控）必须不成立",
    ["parse_skill_form" not in _bad_names, 1 >= 2], [False, False])

# ── D27 两份判据不许漂（2026-10-11）：intent.py 硬拒的那两种 key 误用，服务端也必须【同样不收】。
#    直接 POST 8100 是一条真实存在的绕过路（agent 有网络、文件沙箱管不着 HTTP），所以这条不能只测脚本。
#    真调 form_prefill（自造会话 + 沙盒回填文件，绝不碰线上 form_prefill.json）。
_D27_SID = "session-keycheck"
_D27_FILE = os.path.join(SCRATCH, "keycheck_prefill.json")
_D27_FIX = {"ratio": "16:9", "source_video": "c:\\a.mp4", "product_images": "c:\\p.jpg",
            "product_name": "假发"}
_O27 = (MA.hypit_bridge.intent_map, MA.FORM_PREFILL_PATH)
try:
    MA.hypit_bridge.intent_map = lambda: {
        _D27_SID: {"skill": "super-clone", "task": "kc", "ts": _t.time(), "task_dir": SCRATCH}}
    MA.FORM_PREFILL_PATH = _D27_FILE
    open(_D27_FILE, "w", encoding="utf-8").write("{}")
    _r27 = MA.form_prefill({"session": _D27_SID, "skill": "super-clone", "values": _D27_FIX,
                            "files": [], "notes": {}})
    _rec27 = (json.load(open(_D27_FILE, encoding="utf-8")) or {}).get(_D27_SID) or {}
    chk("D27", "🔴 服务端同一份结论：猜错名与「媒体塞 values」都进不了 defaults（只认字段表），"
        "脚本判红的那些 key 服务端一个都不收",
        [bool(_r27.get("ok")), sorted(_rec27.get("defaults") or {}) == ["product_name"],
         {"ratio", "source_video"} <= set(_rec27.get("dropped") or []),
         bool(_pk(_D27_FIX, _ALLK, _MEDIASK))],
        [True, True, True, True])
    chk("D27b", "丢掉的必须【看得见】：门控答复把 prefill_dropped 原样带出来"
        "（藏起来 = 下一次还是静默；这条和「空表要自己说清没回填」是同一个要求）",
        ["prefill_dropped" in MA.form_gate_view(_D27_SID)], [True])
    _clean = dict(_vw["template"]["values"])     # 用 fields 自己给出的那份模板当合法样例（不另抄一遍）
    _m27 = MA.form_prefill({"session": _D27_SID, "skill": "super-clone",
                            "values": _clean, "files": [], "notes": {}})
    _rec27b = (json.load(open(_D27_FILE, encoding="utf-8")) or {}).get(_D27_SID) or {}
    chk("D27c", "反向对照·同一份判据不是恒含 dropped：换成【fields 模板给的合法样例】再跑一次 ⇒ "
        "defaults 收齐、dropped 变空（两向都验，才证明上面那三条来路是输入，不是抄来的期望）",
        [bool(_m27.get("ok")), _rec27b.get("dropped") == [],
         set(_rec27b.get("defaults") or {}) == set(_clean)], [True, True, True])
finally:
    MA.hypit_bridge.intent_map, MA.FORM_PREFILL_PATH = _O27

# ── D28 入口自己读表（2026-10-11 五刀之①）：agent 不再是"表单 → 命令行"的人肉转录层 ──
# 现网形状：21:04 那次它把绝对路径【手抄】进 driver（抄对了），20:55 那次把 key 名抄成另一条线的叫法
# （source_video/ratio）⇒ 抄这个动作本身就是猜的来源。这条改动把"抄"删掉，所以判据要钉住三件事：
# 优先级（命令行赢）、空值不算给过（不许把空串当"客户选了"）、表里多出来的格不许静默丢。
_ap = _sc_ns["apply_form_values"]
_am = _sc_ns["missing_required"]
_p28 = _sc_ns["build_parser"]()
_md28 = _sc_ns["multi_dests"](_p28)
_K28 = {a.dest: None for a in _p28._actions if a.dest and a.dest != "help"}
_G28 = {"submitted": True, "task_dir": r"C:\dsh\productions\t1",
        "params": {"video": r"C:\dsh\productions\t1\inputs\video\01-aabb.mp4",
                   "product_images": [r"C:\dsh\productions\t1\inputs\product_images\01-cc.jpg"],
                   "product_name": "假发", "customer_request": "换整头", "voice_mode": "preserve_original",
                   "aspect_ratio": "adaptive", "scene_text": "", "voice_script": "客户要的口播全文"}}
_v28, _pr28, _un28 = _ap(dict(_K28, product_name="命令行给的"), _G28, _md28)
# 来源行是 "key␣␣来源" 的形状 ⇒ 按 key 查，不拿"第几条"当判据（顺序会变，按位置断言必假红）
_src28 = {x.split()[0]: x.split()[-1] for x in _pr28}
chk("D28", "🔴 合并规则值表：命令行显式给的赢、其余从表单取、空串不算给过、媒体按槽形归一、"
    "task_id/output_dir 从任务目录落、表里多出来的格【点名不静默】",
    [_v28["product_name"], _src28.get("product_name"), _src28.get("video"), _src28.get("task_id"),
     _v28["video"], _v28["product_images"], _v28["scene_text"], _v28["output_dir"],
     _un28, _am(_v28)],
    ["命令行给的", "命令行", "表单", "任务目录",
     r"C:\dsh\productions\t1\inputs\video\01-aabb.mp4",
     [r"C:\dsh\productions\t1\inputs\product_images\01-cc.jpg"], None,
     os.path.join(r"C:\dsh\productions\t1", "outputs"),
     ["voice_script=客户要的口播全文"], []])
_v28b, _pr28b, _ = _ap(dict(_K28), dict(_G28, params={"product_name": "假发"}), _md28)
chk("D28b", "缺格必须点名（不许静默补一个默认值 —— 那等于替客户做决定，还一路走到报价）；"
    "task_id/output_dir 从任务目录推出来 ⇒ 不该出现在缺的名单里",
    [sorted(_am(_v28b)), any(x.split()[0] == "product_name" and x.split()[-1] == "表单" for x in _pr28b)],
    [["product_images", "video"], True])
chk("D28c", "正向对照（防误杀）：同一份输入里命令行与表单都给了 ⇒ 命令行的值留在最终参数表上，"
    "且来源行说得清是谁给的",
    [_src28.get("product_name"),
     _ap(dict(_K28, product_name="命令行给的"), _G28, _md28)[0]["product_name"]], ["命令行", "命令行给的"])
_m28 = SC_RUN[SC_RUN.index("def apply_form_values"):SC_RUN.index("def missing_required")]
_ns28 = {"multi_dests": _sc_ns["multi_dests"]}
exec(compile(_m28.replace('if vals[key] not in (None, "", []):', "if False:"),
             "mutated", "exec"), _ns28)
chk("D28d", "变异对照：把「命令行给了就跳过」那句改死 ⇒ 命令行值被表单覆盖（证明优先级那条真在起作用）",
    [_ns28["apply_form_values"](dict(_K28, product_name="命令行给的"), _G28, _md28)[0]["product_name"]],
    ["假发"])
# 数 required=True 必须【只数代码】：注释里写着"以前它是 required=True"，整份文本数会被自己的说明喂红
_bp_code = "\n".join(l for l in SC_RUN[SC_RUN.index("def build_parser"):SC_RUN.index("def main(")].splitlines()
                     if not l.strip().startswith("#"))
chk("D28e", "入口面：--from-form / --stage 两个开关在、四格 required 已交给表单（但缺格由 missing_required 拒）",
    [("--from-form" in SC_RUN), ("--stage" in SC_RUN),
     _bp_code.count("required=True"), "_miss = missing_required(vals)" in SC_RUN],
    [True, True, 0, True])
chk("D28f", "档位就是合法出口：--stage 走 stop_after 传给管线；compile 早退自己声明 0 消耗；"
    "wait 是第 5 步「拿到视频」的出口（只捞不投）—— 少一档，遵守规则的人就只能绕开入口手搓",
    ["stop_after=(" in SC_RUN, 'choices=["compile", "submit", "wait"]' in SC_RUN,
     "compiled_only" in SC_RUN, 'a.stage == "wait"' in SC_RUN], [True, True, True, True])

# 🔴 D28g：档位必须【一路传到底】。现网 super-clone-20261012-1824（18:44）：`--stage compile`
#    在"按长度切段"那条递归里没带 stop_after ⇒ 每一块都走到提交那一刀，
#    只是当时没有批准条子才被网关拦回 approval_required —— 有条子就是真花 60 分/段。
#    判据用 AST 找那次调用，不拿字符串数（文本里到处是 stop_after，数得再多次都可能对不上号）。
import ast as _ast28                                                   # noqa: E402
_W3 = open(r"D:\video-skill\backend\services\super_clone_wan3.py", encoding="utf-8").read()
_tree28 = _ast28.parse(_W3)
_chunk_fn = next((n for n in _ast28.walk(_tree28)
                  if isinstance(n, _ast28.AsyncFunctionDef) and n.name == "_run_one_chunk"), None)
_recursive = [n for n in _ast28.walk(_chunk_fn)
              if isinstance(n, _ast28.Call) and getattr(n.func, "id", "") == "run_pipeline"] if _chunk_fn else []
_kw28 = {k.arg for c in _recursive for k in c.keywords}
chk("D28g", "按长度切段的递归里也带 stop_after（递归调 run_pipeline 的关键词名单必须包含它）",
    [bool(_chunk_fn), len(_recursive), "stop_after" in _kw28], [True, 1, True])
_un28 = _ast28.unparse(_chunk_fn) if _chunk_fn else ""
chk("D28h", "递归回来是 compiled_only 时【先早退、再拼接】—— 顺序反了会把 0 分编译报成『生成失败，无法拼接』",
    ["compiled_only" in _un28, "fit_clip_duration" in _un28,
     bool(_un28) and _un28.index("compiled_only") < _un28.index("fit_clip_duration")],
    [True, True, True])
chk("D28i", "多段收口也认档位：stop_after=compile 时直接返回 compiled_only（不拼 fitted、不出号）",
    ["if stop_after == \"compile\":" in _W3, _W3.count('"compiled_only": True') >= 3], [True, True])

# ── D30 handoff.py（五刀之②）：一条命令交接，且【一个模型都不调】——这是"别拿分析当弹表前置"的落点 ──
sys.path.insert(0, HERE)
import handoff as HF                                                     # noqa: E402
_MF29 = [{"key": "video", "accept": ["mp4", "mov"], "max": 1},
         {"key": "product_images", "accept": ["jpg", "png"], "max": 10},
         {"key": "model_image", "accept": ["jpg", "png"], "max": 10},
         {"key": "voice_audio", "accept": ["mp3", "wav"], "max": 5}]
_f29a, _s29a = HF.slot_plan(_MF29, [r"C:\t\inputs\a.mp4"])
_f29b, _s29b = HF.slot_plan(_MF29, [r"C:\t\inputs\a.jpg"])
_f29c, _s29c = HF.slot_plan(_MF29, [r"C:\t\inputs\a.mp4", r"C:\t\inputs\b.mp4"])
_f29d, _s29d = HF.slot_plan(_MF29, [r"C:\t\inputs\1.wav", r"C:\t\inputs\2.mp3"])
_f29e, _s29e = HF.slot_plan(_MF29, [r"C:\t\inputs\note.txt"])
chk("D30", "🔴 归位只填【无歧义】的：一个文件只对一个槽才挂；一图两槽（产品图/脸图）⇒ 留空给客户选；"
    "单值槽来了两张图 ⇒ 整格留空；多值槽按上限截断；扩展名对不上任何槽 ⇒ 不动它",
    [[x["field"] for x in _f29a], sorted(_s29a), [x["field"] for x in _f29b], bool(_s29b),
     [x["field"] for x in _f29c], sorted(_s29c), sorted(x["field"] for x in _f29d), sorted(_s29e)],
    [["video"], [], [], True, [], ["video"], ["voice_audio", "voice_audio"], ["note.txt"]])
_HS = open(os.path.join(HERE, "handoff.py"), encoding="utf-8", errors="replace").read()
_HS_CODE = "\n".join(l for l in _HS.splitlines() if not l.strip().startswith("#"))
chk("D30b", "handoff 真的【一个模型都不调】：不 import 网关、不打 video.analyze、不 POST 8100 —— "
    "它只登记 + 归位 + 打印（这条是 44 分钟空等的正解：把机械动作和判断分开，判断挪到交表之后）",
    ["gateway.py" not in _HS_CODE, "video.analyze" not in _HS_CODE,
     "urllib.request.Request(" not in _HS_CODE, _HS_CODE.count("urlopen") == 1,
     "intent.py" in _HS_CODE], [True, True, True, True, True])
chk("D30c", "归位走的是同一条回填口（intent.py prefill）⇒ 复制/sha8 命名/服务端判 key 只有一份；"
    "handoff 里不许出现第二份复制逻辑",
    ['INTENT, "prefill"' in _HS_CODE, "shutil.copyfile" not in _HS_CODE,
     "form_prefill" not in _HS_CODE], [True, True, True])
chk("D30d", "反向对照：D30 那几条「不许出现」不是恒真 —— 违规样例（自己调网关、自己复制文件、自己 POST 8100）"
    "喂进同一组探针必须全都不成立",
    ["gateway.py" not in 'subprocess.run([PY, "gateway.py", "call", "video.analyze"])',
     "shutil.copyfile" not in "shutil.copyfile(a, b)",
     "urllib.request.Request(" not in "urllib.request.Request(u, data=b'x')"], [False, False, False])

# ---- D31 入口不许在客户桌面上留黑框，也不许给同一条任务起第二台（2026-10-12 现网两条）----
import tempfile                                          # noqa: E402

_TD31 = tempfile.mkdtemp(prefix="dsh-coll-")
os.makedirs(os.path.join(_TD31, "outputs"), exist_ok=True)
_PF31 = os.path.join(_TD31, ".compile.pid")
# 用一个【自己生的】活进程当"别人那台"——拿现网某个 pid 当 fixture = 钉现场，别人一死这条就随机红
_LIVE31 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def _coll31(pid_text, with_gate=True):
    with open(_PF31, "w", encoding="utf-8") as f:
        f.write(pid_text)
    return _sc_ns["compile_collision"]({"task_dir": _TD31} if with_gate else None,
                                       os.path.join(_TD31, "outputs"))


chk("D31", "牌子判据值表：死 pid / 本进程自己 / 脏牌子 ⇒ 允许起；活的别人 ⇒ 冲突；没给 gate 只靠 output_dir 也要认出来",
    [_coll31("4242")[0], _coll31(str(os.getpid()))[0], _coll31("not-a-pid")[0],
     _coll31(str(_LIVE31.pid))[0], _coll31(str(_LIVE31.pid), False)[0]],
    [0, 0, 0, _LIVE31.pid, _LIVE31.pid])
_LIVE31.kill()
_LIVE31.wait()

# ---- D31e 🔴 祖先那一支不算撞车（2026-10-12 18:26 现网：一整天"点火零产物"的真死因）----
# 8100 用 `D:\video-skill\.venv\Scripts\pythonw.exe` 起子进程 —— 那是个【重定向壳】：
# 实测 `Popen.pid=65192`，而真正执行 run.py 的是它的子进程 55020。牌子写的是壳的号 ⇒
# run.py 一读牌子 = 我自己爸、判活为真 ⇒ **把自己拒了**，outputs/ 空，
# compile.log 里那句【已经在跑 pid=58268】是它自己打印的。这条只能真跑 subprocess 验
# （父子关系在闸自己进程里造不出来）。
_ANC31 = r'''
import importlib.util, json, os, subprocess, sys
spec = importlib.util.spec_from_file_location("sc_anc", sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
d, mode = sys.argv[2], sys.argv[3]
pidf = os.path.join(d, ".compile.pid")
kid, wrote = None, 0
if mode == "parent":
    wrote = os.getppid()
    open(pidf, "w").write(str(wrote))                   # 牌子 = 把我起来的那个壳（现网形状）
else:
    kid = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(25)"])
    wrote = kid.pid
    open(pidf, "w").write(str(wrote))                   # 牌子 = 一个活着的【外人】
got = list(m.compile_collision({"task_dir": d}, ""))
if kid:
    kid.kill(); kid.wait()
print(json.dumps({"wrote": wrote, "collision": got, "is_parent": wrote == os.getppid()}))
'''
_ANC31F = os.path.join(_TD31, "anc.py")
with open(_ANC31F, "w", encoding="utf-8") as f:
    f.write(_ANC31)
_run31mod = os.path.join(SKILLS_DIR, "super-clone", "runner", "run.py")
_a1 = subprocess.run([sys.executable, _ANC31F, _run31mod, _TD31, "parent"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
_a2 = subprocess.run([sys.executable, _ANC31F, _run31mod, _TD31, "foreign"],
                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)


def _anc31(r):
    try:
        j = json.loads((r.stdout or "").strip())
    except ValueError:
        return {"wrote": 0, "collision": ["-", "-"], "is_parent": "没输出/解析不了: " + (r.stderr or "")[-120:]}
    return {"wrote": j.get("wrote"), "collision": j.get("collision"), "is_parent": j.get("is_parent")}


_p31, _f31 = _anc31(_a1), _anc31(_a2)
chk("D31e", "牌子=我自己父亲（venv 重定向壳）⇒ 不算撞车（否则 8100 点火那台会把自己拒了、零产物）",
    [_p31["is_parent"], _p31["collision"]], [True, [0, ""]])
chk("D31e2", "反向：牌子=一个活着的外人 ⇒ 照旧判冲突（两向一起才算数，只测一边=恒绿半边）",
    [_f31["is_parent"], _f31["collision"][0] == _f31["wrote"],
     str(_f31["collision"][1]).endswith(".compile.pid")], [False, True, True])
# 真跑 CLI 那一条要另开一台"别人"（上面那台已经按脚本 kill 掉了 ⇒ 拿它当牌子就测不到冲突，
# 只会让入口往下走、错在别的校验上 —— 这就是 got=[True, False, ...] 的形状）
_LIVE31B = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
with open(_PF31, "w", encoding="utf-8") as f:
    f.write(str(_LIVE31B.pid))
_cr31 = subprocess.run(
    [sys.executable, os.path.join(SKILLS_DIR, "super-clone", "runner", "run.py"),
     "--video", os.path.join(_TD31, "x.mp4"), "--product-images", os.path.join(_TD31, "p.png"),
     "--product-name", "闸里的假任务", "--output-dir", os.path.join(_TD31, "outputs")],
    capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
_io31 = _cr31.stdout + _cr31.stderr
# 🔴 断言只吃【ASCII 标记】：子进程被重定向后按 cp936 写 stderr，父进程按 utf-8 解就是乱码 ——
#    拿中文子串当判据会红在自己的解码上（现网这条第一次跑就是这么红的）。"pid=" + ".compile.pid"
#    + "compile.log" 三个只有撞牌子那条路会同时出现，够点名了。
chk("D31b", "真跑入口：这条任务已有一台在跑 ⇒ 退出非 0、说清『不起第二台』，且 outputs 里一个文件都没写"
    "（拒必须发生在 import 引擎之前 —— 晚一步 MiMo 的钱就花出去了）",
    [_cr31.returncode != 0, all(x in _io31 for x in ("pid=", ".compile.pid", "compile.log")),
     os.listdir(os.path.join(_TD31, "outputs")) == [], "run_pipeline" not in _io31],
    [True, True, True, True])
_LIVE31B.kill()
_LIVE31B.wait()
try:
    os.remove(_PF31)
except OSError:
    pass
_m31 = SC_RUN[SC_RUN.index("def main("):]
chk("D31c", "顺序钉死：摘控制台窗口是 main 的第一句，撞牌子的检查排在 import 引擎之前；"
    "并且留了一条给人排障的出口（SUPER_CLONE_SHOW_CONSOLE）",
    [0 < _m31.index("_hide_console_window()") < _m31.index("compile_collision(") < _m31.index(
        "_ensure_video_skill_on_path()"), "SUPER_CLONE_SHOW_CONSOLE" in SC_RUN], [True, True])
_m31b = _m31.replace("_hide_console_window()", "pass  #", 1).replace(
    "compile_collision(_gate, vals.get(\"output_dir\"))", "(0, \"\")", 1)
chk("D31d", "反向对照：把摘窗那行和牌子检查各删一次 ⇒ 上面那条顺序断言必须红（不红就是它本来就恒真）",
    [("_hide_console_window()" in _m31b) and (0 < _m31b.index("_hide_console_window()")),
     "compile_collision(" in _m31b], [False, False])
shutil.rmtree(_TD31, ignore_errors=True)

# ---- D32 `--stage wait`：第 5 步「拿到视频」的合法出口（只捞不投）----------------------
# 现网形状（session-5282eabf）：agent 的前台命令被工具 10 分钟上限强杀，而一条 wan3-prime 生成
# 实测 10–17 分钟 ⇒ 提交必然被打断；没有"只按号捞"这一档，它剩下的唯一动作就是再 call 一次（双花）。
_WT = tempfile.mkdtemp(prefix="dsh-wait-")
_WS = os.path.join(_WT, "outputs", "step2_wan3")
os.makedirs(_WS, exist_ok=True)
_WJOB, _WMP4 = os.path.join(_WS, "wan3_job.json"), os.path.join(_WS, "seg01-prime.mp4")


def _wj32(tid="prime_deadbeef", dest=_WMP4, raw=None):
    with open(_WJOB, "w", encoding="utf-8") as f:
        f.write(raw if raw is not None else json.dumps(
            {"kind": "wan3_job", "task_id": tid, "output_path": dest}))


_wj = _sc_ns["find_pending_jobs"]
_a32 = [_wj(_WT) == []]                                    # ① 没号 ⇒ 空
_wj32()
_a32.append([j["job"] for j in _wj(_WT)] == ["prime_deadbeef"])   # ② 有号、成片不在 ⇒ 该捞
open(_WMP4, "wb").write(b"v")
_a32.append(_wj(_WT) == [])                                # ③ 已落盘 ⇒ 不重复捞
_wj32(raw='{"kind":"other"}')
_a32.append(_wj(_WT) == [])                                # ④ 不是那份形状 ⇒ 忽略
_wj32(raw="{坏 json")
_a32.append(_wj(_WT) == [])                                # ⑤ 坏 json ⇒ 忽略且不抛
try:
    os.remove(_WMP4)
except OSError:
    pass
chk("D32", "号判据值表：没号⇒空 / 有号且成片不在⇒该捞 / 已落盘⇒不重复捞 / 形状不对与坏 json⇒忽略不抛",
    _a32, [True] * 5)
_w1 = subprocess.run([sys.executable, os.path.join(SKILLS_DIR, "super-clone", "runner", "run.py"),
                      "--stage", "wait", "--output-dir", os.path.join(_WT, "outputs")],
                     capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
                     env=dict(os.environ, PYTHONIOENCODING="utf-8"))
_w1t = (_w1.stdout or "") + (_w1.stderr or "")
chk("D32b", "真跑：没号 ⇒ 退非 0 且明说「wait 不是重投的口子」，一个请求都不发"
    "（缺号还往下走 = 拿只捞当重投，那是双花的另一条路）",
    [_w1.returncode != 0, "不是重投" in _w1t, "product" not in _w1t.lower()], [True, True, True])
_wj32()
_w2 = subprocess.run([sys.executable, "-c", """
import importlib.util, io, json, os, sys
sys.path.insert(0, r"D:\\video-skill")
spec = importlib.util.spec_from_file_location("w2", r"%s")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import backend.gateway as gw
calls = []
def fake(cap, purpose, account, params):
    calls.append({"cap": cap, "purpose": purpose, "params": dict(params)})
    return {"ok": False, "result": {"kind": "poll_running"}, "credits": {"charged": 0}, "error": "桩"}
gw.invoke = fake
try:
    rc = m.main(["--stage", "wait", "--output-dir", r"%s"])
except SystemExit as e:
    rc = e.code if isinstance(e.code, int) else 9
io.open(r"C:\\dsh\\scripts\\_wait_calls.json", "w", encoding="utf-8").write(
    json.dumps({"rc": rc, "calls": calls}, ensure_ascii=False))
""" % (os.path.join(SKILLS_DIR, "super-clone", "runner", "run.py"), os.path.join(_WT, "outputs"))],
                     capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
_w2f = os.path.join(HERE, "_wait_calls.json")
_w2j = json.load(open(_w2f, encoding="utf-8")) if os.path.isfile(_w2f) else {}
if os.path.isfile(_w2f):
    os.remove(_w2f)
_c0 = (_w2j.get("calls") or [{}])[0]
chk("D32c", "真跑（gateway 打桩、绝不发真请求）：有号 ⇒ 恰好一次 invoke，params 带 job_id、"
    "【不带 prompt】⇒ 物理上提交不出去（适配器认 job_id 才走 poll 模式）",
    [len(_w2j.get("calls") or []), _c0.get("params", {}).get("job_id"),
     "prompt" in _c0.get("params", {}), _w2j.get("rc") != 0, (_w2.stdout or "") + (_w2.stderr or "") != ""],
    [1, "prime_deadbeef", False, True, True])
chk("D32d", "顺序钉死：wait 分支走在 missing_required 之前 —— 它不需要原片/产品图那五格（号在盘上），"
    "拿必填拦它等于逼人再提交一次",
    0 < _m31.index('a.stage == "wait"') < _m31.index("missing_required(vals)"), True)
shutil.rmtree(_WT, ignore_errors=True)

# ---- D33 已有任务号就不许再提交（双花的唯一屏障：看盘，不看它用什么姿势拼参数）----------
# 现网 13:25:22：agent 拿 outputs/…/_wan3_request.json（物证转储）去敲一个【不存在的】
# `gateway.py submit-existing` 想重投。它为什么编？因为当时流程里没有"只捞不投"的出口。
# 出口现在有了（--stage wait），这一组钉的是另一半：有号没片 ⇒ 提交这一档必须物理关上。
_OT = tempfile.mkdtemp(prefix="dsh-owed-")
_OS = os.path.join(_OT, "outputs", "step2_wan3")
os.makedirs(_OS, exist_ok=True)
_OJ, _OMP = os.path.join(_OS, "wan3_job.json"), os.path.join(_OS, "seg01-prime.mp4")
_a33 = [_sc_ns["submitted_but_unpaid"](_OT) == []]           # ① 没号 ⇒ 不拦
with open(_OJ, "w", encoding="utf-8") as _f33:
    _f33.write(json.dumps({"kind": "wan3_job", "task_id": "prime_OWED", "output_path": _OMP}))
_a33.append(_sc_ns["submitted_but_unpaid"](_OT) == [("prime_OWED", _OMP)])   # ② 有号没片 ⇒ 拦
with open(_OMP, "wb") as _f33b:
    _f33b.write(b"v")
_a33.append(_sc_ns["submitted_but_unpaid"](_OT) == [])       # ③ 已落盘 ⇒ 放开
_a33.append(_sc_ns["submitted_but_unpaid"]("") == [])        # ④ 没有目录 ⇒ 空，不抛
chk("D33", "判据值表：没号⇒不拦 / 有号没片⇒拦（返回号与落点）/ 成片已落盘⇒放开（补跑别的段不许被永久锁死）"
    " / 空目录⇒空表不抛", _a33, [True, True, True, True])
os.remove(_OMP)
_o1 = subprocess.run([sys.executable, os.path.join(SKILLS_DIR, "super-clone", "runner", "run.py"),
                      "--video", os.path.join(_OT, "x.mp4"), "--product-images", os.path.join(_OT, "p.png"),
                      "--product-name", "闸里的假任务", "--output-dir", os.path.join(_OT, "outputs"),
                      "--stage", "submit"],
                     capture_output=True, text=True, timeout=240, encoding="utf-8", errors="replace",
                     env=dict(os.environ, PYTHONIOENCODING="utf-8"))
_ot1 = _o1.stdout + _o1.stderr
chk("D33b", "真跑 --stage submit（有号没片）⇒ 退非 0、点名 --stage wait、并写明转储不是输入口；"
    "且拒在 import 引擎之前（0 分、0 上游调用）",
    [_o1.returncode != 0, "--stage wait" in _ot1, "_wan3_request.json" in _ot1,
     "run_pipeline" not in _ot1 and "MiMo" not in _ot1], [True, True, True, True])
_m33 = SC_RUN[SC_RUN.index("def main("):]
chk("D33c", "顺序 + 反向对照：这道检查排在 import 引擎之前；把整段删掉 ⇒ 判据必须红（不是恒绿的摆设）",
    [0 < _m33.index("submitted_but_unpaid(_tdir)") < _m33.index("_ensure_video_skill_on_path()"),
     "submitted_but_unpaid(_tdir)" in _m33.replace("submitted_but_unpaid(_tdir)", "", 1)],
    [True, False])
shutil.rmtree(_OT, ignore_errors=True)

n_ok = sum(1 for _, ok in RESULTS if ok)
cprint("\n=== 技能表单闸汇总：%d/%d 通过 ===" % (n_ok, len(RESULTS)))
for cid, ok in RESULTS:
    if not ok:
        cprint("   未通过：%s" % cid)
sys.exit(0 if n_ok == len(RESULTS) else 1)

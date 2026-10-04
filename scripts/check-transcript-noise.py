r"""聊天区「过程行」降噪闸：插件里的 CSS 与内核 DOM 钩子必须还对得上，内核侧不许留残骸。

背景（2026-10-03 用户报"DSH 执行任务时命令类行刷屏"）：
  内核本来就会折叠这些行 —— dsh-client-ui-chat/lib/client.js:1586 的 processWindowReady 里带
  `processPresentation.turnClosed` ⇒ 只有【整回合结束】才折。回合进行中每条 tool-call 都直接铺一行，
  这就是截图里那一堆 Pwsh / 代码 / 读取图片。
  上一轮有人改 dsh-client-ui-tool 去分组 block.subCalls —— 那是【错层】：截图那些是根级 tool-call
  节点（各自一个 ToolCallTree），不是某个工具的子调用，分组对它们无效，还引入过 React hooks 顺序风险。
  所以实现落在我们插件注入的一条 CSS 上（display 层，数据不删；回合结束后内核的「N 次工具调用」照常可展开）。

本闸不依赖浏览器：静态比"规则形状 + 内核属性名 + 内核文件是否回到出厂"。
CSS 的匹配语义本身已用真浏览器验过（夹具 _scratch_b/noise_fixture.html，13 个用例：
  on  ⇒ bash/read/search/write/edit/code 的 ok 行隐藏，error、stopped、running、
        含失败子调用的行、present、subagent、非 tool-call 节点全部保留；off ⇒ 全显）。
"""
import os
import re
import sys

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.environ.get("DSH_APP") or os.path.join(
    os.environ.get("LOCALAPPDATA", r"C:\Users\Administrator\AppData\Local"),
    "Programs", "DSH Desktop", "resources", "app", "node_modules", "@deepseek-ai")
BAK = os.path.join(WS, ".dsh-ui-backup")
PLUGIN = os.path.join(WS, "plugins", "ops-gateway", "lib", "client.js")
PROCESS_VARIANTS = ("bash", "read", "search", "write", "edit", "code")

total, fails = [0], []


def check(name, got, want):
    total[0] += 1
    if got != want:
        fails.append((name, got, want))
        print("FAIL", name, "=>", str(got)[:200], "\n     期望", str(want)[:200])
    else:
        print("PASS", name, "=>", str(got)[:110])


def read(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def main():
    src = read(PLUGIN)
    chat = read(os.path.join(APP, "dsh-client-ui-chat", "lib", "client.js"))
    tool = read(os.path.join(APP, "dsh-client-ui-tool", "lib", "client.js"))
    conv = read(os.path.join(APP, "dsh-client-ui-conversation", "lib", "client.js"))
    deli = read(os.path.join(APP, "dsh-client-ui-deliverables", "lib", "client.js"))

    m = re.search(r"const NOISE_CSS = (.*?);\n", src, re.S)
    if not m:
        check("0 插件里有 NOISE_CSS", False, True)
        return report()
    css = "".join(eval("(" + m.group(1) + ")"))

    # ── ① 规则形状：可翻的开关 + 只收过程变体 ──────────────────────────────────────
    check("1 折叠挂在可翻开关上（:root[data-weeksir-noise=\"on\"]），不是一刀切硬删",
          css.startswith(':root[data-weeksir-noise="on"] '), True)
    check("1a 只认 tool-call 这一种节点（别的 kind 一律不碰）",
          '[data-chat-flow-kind="tool-call"]' in css, True)
    for v in PROCESS_VARIANTS:
        check("1b 过程变体 %s 在收的范围内" % v, ':has([data-variant="%s"])' % v in css, True)
    check("1c 变体个数就是 6 个（多收一个=可能把交付物也藏了）",
          len(set(re.findall(r'data-variant="([a-z]+)"', css))), 6)
    check("1d others 不在名单里（present 交付物、cordis_*、subagent 都落在 others）",
          'data-variant="others"' in css, False)

    # ── ② 保留条款：错误/停止/进行中/子调用失败，一个都不许跟着藏 ──────────────────
    check("2 只折 ok 状态的行", ':has([data-state="ok"])' in css, True)
    check("2b 出错行必须留", ':not(:has([data-state="error"]))' in css, True)
    check("2c 中断行必须留", ':not(:has([data-state="stopped"]))' in css, True)
    check("2d running 不在规则里 ⇒ 进行中与含失败子调用的行都不藏",
          'data-state="running"' in css, False)

    # ── ③ 开关与逃生口真的接在运行时上 ─────────────────────────────────────────────
    check("3 style 标签有固定 id（重复 apply 不堆叠）", 'tag.id = "weeksir-process-noise"' in src, True)
    check("3b 运行时可翻：window.weeksirProcessRows(bool)", "window.weeksirProcessRows" in src, True)
    check("3c 持久开关走 localStorage（排查时不用改代码）",
          'localStorage.getItem("weeksir_process_rows")' in src, True)
    check("3d 开关写的是 data-weeksir-noise（与 CSS 同源，不是两套状态）",
          src.count("dataset.weeksirNoise") >= 2, True)

    # ── ④ 内核 DOM 钩子还在不在（升级改了属性名 ⇒ 这里先红，而不是默默失效）────────
    check("4 内核仍印 data-chat-flow-kind", '"data-chat-flow-kind"' in chat, True)
    check("4b 内核仍印 data-variant / data-tool / data-state",
          all(k in tool for k in ('"data-variant"', '"data-tool"', '"data-state"')), True)
    check("4c 内核折叠条件仍带 turnClosed（这就是【进行中不折】的根因，别当成已修）",
          "turnClosed" in chat, True)

    # ── ⑤ 错层补丁不许再出现（上次那个改内核 JS 的分组实现）───────────────────────
    check("5 内核 ui-tool 里没有分组合影（splitProcessCalls/ExecutionDetailsGroup 已回滚）",
          re.findall(r"splitProcessCalls|ExecutionDetailsGroup|processCallGroup", tool), [])
    check("5b 内核 locale 里没有孤儿「执行细节」键（实现搬回插件层了）",
          "executionDetails" in conv, False)
    check("5c 我们也不去抢内核 JS：插件没注册 conversation.chat.node",
          "conversation.chat.node" in src, False)

    # ── ⑥ 只是显示层：数据没删、仍可展开 ──────────────────────────────────────────
    check("6 唯一动作是 display:none（不摘 DOM）", re.findall(r"\{display:none!important\}", css) != [], True)
    check("6b 没有用 MutationObserver 搬 DOM 的代码", "MutationObserver" in src, False)

    # ── ⑦ 交付行文案（用户定：别叫"交付文件"这种系统行，要像结果）─────────────────
    for key, want in [("row.title", "结果文件"), ("row.ok", "已生成"),
                      ("row.running", "正在生成"), ("row.error", "生成失败")]:
        check("7 交付行文案 %s 已是结果口（%s）" % (key, want), '"%s": "%s",' % (key, want) in deli, True)
    check("7b 旧的「交付文件」标题不再出现在行渲染处", '"row.title": "交付文件"' in deli, False)
    check("7c 文案改动只动 locale，没动 PresentRow 的推导逻辑",
          "function PresentRow" in deli and "fileNames(args)" in deli, True)

    # ── ⑧ 内核侧残留清点：JS 必须逐字节等于出厂，只有 locale 被我们改过 ────────────
    tool_bak = read(os.path.join(BAK, "dsh-client-ui-tool.client.js.bak"))
    check("8 内核 ui-tool 已逐字节回到出厂（错层补丁没留残骸）",
          bool(tool_bak) and tool_bak == tool, True)
    conv_bak = read(os.path.join(BAK, "dsh-client-ui-conversation.client.js.bak"))
    orphans = [l for l in conv_bak.splitlines() if l not in conv.splitlines()]
    check("8b 内核 ui-conversation 与出厂只差那 6 行孤儿 locale（没删别的）",
          [len(orphans), all("executionDetails" in l for l in orphans)], [6, True])
    deli_bak = read(os.path.join(BAK, "dsh-client-ui-deliverables.client.js.bak"))
    diff = [l for l in deli_bak.splitlines() if l not in deli.splitlines()]
    check("8c 内核 ui-deliverables 只改了 8 条 locale 行（zh4+en4 的 row.*），逻辑行零改动",
          [len(diff), all('"row.' in l for l in diff)], [8, True])
    return report()


def report():
    print("\n=== 降噪闸：%d 条断言，失败 %d ===" % (total[0], len(fails)))
    for n, g, w in fails:
        print("  FAIL", n, "got", str(g)[:120], "want", str(w)[:120])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

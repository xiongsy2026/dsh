#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""官方交付卡补丁：原生菜单加【下载图片/下载视频】+ 卡片内加【你还可以】一行。

下载项的文案按【文件真实类型】取词（2026-10-10 订正）：原先只有一条写死的
"presented.download": "下载视频"，png/jpg 也吃这一句 ⇒ 客户指着帧图问"图片为什么是下载视频"。
类型判定不自己抄表，复用官方 primitives.classifyFileType（同一张卡上 FileTypeIcon 选图标用的就是它）。

🔴 本脚本是这条补丁的【唯一入口】。早先那份 patch-deliverables-download.py 已退役
   （挪成 _retired-patch-deliverables-download.py.bak）——它写死的正是这次修掉的那句"下载视频"，
   留着就是第二个入口，谁跑一遍就把 bug 装回去。

分工（🔴 官方那份里不许出现第二份业务规则）：
  官方 client.js  = 只长 UI：原生 Menu / 原生图标 / 原生 CSS 类 / 原生字典位
  super-clone-ui = 策略，四个可选全局钩子：
      __weeksirDownloadUrl(absPath) -> url|null      这个文件能不能下载、URL 怎么拼
      __weeksirDownload(url)                         怎么触发下载
      __weeksirTurnRecs(matched)   -> [{id,label,tagline}]   本轮产物该推荐哪几条技能
      __weeksirPickRec(id)                           点推荐 = 只把 /技能 填进输入框
⇒ 任何钩子缺席都退回原生行为（空数组 / 整行不画），且全部包在 try 里：
   官方组件抛错 = Cordis 判整棵插件树失败 = 桌面端崩溃重启循环（2026-10-08 whale-girl 的教训）。

前提：目标文件必须是【未打过的原版】。检测到任何 weeksir 标记就拒绝落盘（不留半补丁状态），
     要重打就先还原 client.js.bak-weeksir-download-20261008。
"""
import io
import os
import shutil
import sys

# 🔴 cp936 控制台上 print("… ⇒ …") 会把补丁脚本自己崩掉：拒绝的理由就看不见了（崩 ≠ 拒得清楚）。
#    只换 errors，不换 encoding。
try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

TARGET = (r"C:\Users\Administrator\AppData\Roaming\dsh-desktop\harness\profiles\node_modules"
          r"\@deepseek-ai\dsh-client-ui-deliverables\lib\client.js")
PRISTINE_BAK = TARGET + ".bak-weeksir-download-20261008"
T = "\t"
REV = "weeksir-card-20261010a"

HELPER = [
    T * 2 + "// ==== weeksir 补丁 %s：交付卡【下载图片/下载视频】+【你还可以】====" % REV,
    T * 2 + "// 回退：还原同目录 client.js.bak-weeksir-download-20261008（原版），或删掉本块 + 三处 weeksir 标记。",
    T * 2 + "// 🔴 钩子缺席 ⇒ 空数组 / 整行不画 ⇒ 与原生一致，绝不画一个点了没反应的项。",
    T * 2 + "// 🔴 每个钩子调用都包 try：官方组件抛错会打死整棵插件树（whale-girl 2026-10-08 的教训）。",
    T * 2 + "function weeksirHook(name) {",
    T * 3 + "try {",
    T * 4 + 'const fn = typeof window !== "undefined" && window[name];',
    T * 4 + 'return typeof fn === "function" ? fn : null;',
    T * 3 + "} catch (e) {",
    T * 4 + "return null;",
    T * 3 + "}",
    T * 2 + "}",
    T * 2 + "function weeksirDownloadUrl(absPath) {",
    T * 3 + "const fn = weeksirHook(\"__weeksirDownloadUrl\");",
    T * 3 + "try {",
    T * 4 + "return fn ? fn(absPath) || null : null;",
    T * 3 + "} catch (e) {",
    T * 4 + "return null;",
    T * 3 + "}",
    T * 2 + "}",
    T * 2 + "function weeksirTriggerDownload(url) {",
    T * 3 + "const fn = weeksirHook(\"__weeksirDownload\");",
    T * 3 + "try {",
    T * 4 + "if (fn) fn(url);",
    T * 3 + "} catch (e) {}",
    T * 2 + "}",
    T * 2 + "function weeksirTurnRecs(matched) {",
    T * 3 + "const fn = weeksirHook(\"__weeksirTurnRecs\");",
    T * 3 + "try {",
    T * 4 + "const list = fn ? fn(matched) : null;",
    T * 4 + "return Array.isArray(list) ? list : [];",
    T * 3 + "} catch (e) {",
    T * 4 + "return [];",
    T * 3 + "}",
    T * 2 + "}",
    T * 2 + "function weeksirPickRec(id) {",
    T * 3 + "const fn = weeksirHook(\"__weeksirPickRec\");",
    T * 3 + "try {",
    T * 4 + "if (fn) fn(id);",
    T * 3 + "} catch (e) {}",
    T * 2 + "}",
    # 🔴 类型判定复用官方 primitives.classifyFileType（同一张卡上 FileTypeIcon 选图标用的就是它），
    #    这里不抄第二份扩展名表；但整段包 try：内核升级把该导出摘了也只是退回"下载文件"，
    #    不能让官方组件抛错打死整棵插件树（whale-girl 2026-10-08 的教训，同上面每个钩子的理由）。
    T * 2 + "function weeksirFileKind(absPath) {",
    T * 3 + "try {",
    T * 4 + "const fn = _deepseek_ai_dsh_client_ui_primitives.classifyFileType;",
    T * 4 + "const kind = typeof fn === \"function\" ? String(fn(absPath) || \"\") : \"\";",
    T * 4 + "return kind === \"image\" || kind === \"video\" ? kind : \"file\";",
    T * 3 + "} catch (e) {",
    T * 4 + "return \"file\";",
    T * 3 + "}",
    T * 2 + "}",
    T * 2 + "// ==== end weeksir 补丁 ====",
    "",
]
ANCHOR_FN = T * 2 + "/** File identity and explicit default-app or file-manager actions for one delivery. */"

RECS_COMPONENT = [
    T * 2 + "/** 「你还可以」一行：钩子给空 ⇒ 整行不画。只用官方类（presented/toggle/hostStatus/description），不自绘样式。 */",
    T * 2 + "function weeksirRecsRow({ matched, t }) {",
    T * 3 + "const recs = weeksirTurnRecs(matched);",
    T * 3 + "if (recs.length === 0) return null;",
    T * 3 + 'return (0, react_jsx_runtime.jsxs)("div", {',
    T * 4 + "className: Deliverables_module_css_default.presented,",
    T * 4 + '"data-weeksir-recs-row": "%s",' % REV,
    T * 4 + '"data-single": true,',
    T * 4 + 'children: [(0, react_jsx_runtime.jsx)("span", {',
    T * 5 + "className: Deliverables_module_css_default.hostStatus,",
    T * 5 + 'children: t("presented.recsLabel")',
    T * 4 + '})].concat(recs.map((rec) => (0, react_jsx_runtime.jsxs)("button", {',
    T * 5 + 'type: "button",',
    T * 5 + "className: Deliverables_module_css_default.toggle,",
    T * 5 + '"data-rec-skill": String(rec && rec.id || ""),',
    T * 5 + "onClick: () => {",
    T * 6 + "weeksirPickRec(rec && rec.id);",
    T * 5 + "},",
    T * 5 + 'children: [String(rec && rec.label || rec && rec.id || ""), rec && rec.tagline ? (0, react_jsx_runtime.jsx)("span", {',
    T * 6 + "className: Deliverables_module_css_default.description,",
    T * 6 + "children: rec.tagline",
    T * 6 + "}) : null]",
    T * 4 + "}, String(rec && rec.id))))",     # 四个右括号：String 自己 1 个 + jsxs 调用 + map + concat
    T * 3 + "});",
    T * 2 + "}",
    "",
]
ANCHOR_CARD_FN = T * 2 + "function PresentedFileCard({ file, cwd, phase, host, onPreview, onAction, t }) {"

CONST_LINES = [
    T * 3 + "const weeksirDl = weeksirDownloadUrl(resolveWorkspacePath(cwd, file.path));",
    T * 3 + 'const weeksirDlKind = weeksirDl ? weeksirFileKind(file.path) : "";',
]
ANCHOR_STATUS = T * 3 + "const status = phase === void 0 ? cardDescription("

ITEMS_TAIL = T * 8 + "}],"
ONSELECT = [
    T * 8 + "onSelect: (id) => {",
    T * 9 + 'act(id === "reveal" ? "reveal" : "open");',
    T * 8 + "}",
]
ITEMS_NEW = [
    T * 8 + "}].concat(weeksirDl ? [{",
    T * 9 + 'id: "weeksir-download",',
    T * 9 + "icon: (0, react_jsx_runtime.jsx)(_deepseek_ai_dsh_client_ui_primitives.IconDownloadOutline16, {",
    T * 10 + "size: 16,",
    T * 10 + "className: Deliverables_module_css_default.menuActionIcon",
    T * 9 + "}),",
    T * 9 + 'label: t(weeksirDlKind === "image" ? "presented.download.image" : weeksirDlKind === "video" ? "presented.download.video" : "presented.download.file")',
    T * 8 + "}] : []),",
]
ONSELECT_NEW = [
    T * 8 + "onSelect: (id) => {",
    T * 9 + 'if (id === "weeksir-download") {',
    T * 10 + "setMenuOpen(false);",
    T * 10 + "previewRef.current?.focus();",
    T * 10 + "weeksirTriggerDownload(weeksirDl);",
    T * 10 + "return;",
    T * 9 + "}",
] + ONSELECT[1:]

RECS_ROW = [
    T * 5 + "// weeksir %s：本轮产物推出的技能（判据/点击全在 super-clone-ui）" % REV,
    T * 5 + "(0, react_jsx_runtime.jsx)(weeksirRecsRow, {",
    T * 6 + "matched,",
    T * 6 + "t",
    T * 5 + "}),",
]
ANCHOR_IN_ROW = T * 5 + 'collapsible && (0, react_jsx_runtime.jsxs)("button", {'

DICT = [
    (T * 3 + '"presented.defaultApp": "用默认应用打开",', [
        T * 3 + '"presented.download.image": "下载图片",',
        T * 3 + '"presented.download.video": "下载视频",',
        T * 3 + '"presented.download.file": "下载文件",',
    ]),
    (T * 3 + '"presented.download.file": "下载文件",', [
        T * 3 + '"presented.recsLabel": "你还可以",',
    ]),
    (T * 3 + '"presented.defaultApp": "Open in default app",', [
        T * 3 + '"presented.download.image": "Download image",',
        T * 3 + '"presented.download.video": "Download video",',
        T * 3 + '"presented.download.file": "Download file",',
    ]),
    (T * 3 + '"presented.download.file": "Download file",', [
        T * 3 + '"presented.recsLabel": "You might also need",',
    ]),
]


def one(indices):
    if len(indices) != 1:
        raise AssertionError("锚点命中 %d 处（要求恰好 1 处）" % len(indices))
    return indices[0]


def eq(lines, needle):
    return [i for i, l in enumerate(lines) if l == needle]


def apply(lines):
    # 🔴 插入一律用 lines[i:i] = [...]；写成 lines[i:0] 会把 [0:i) 整段替换掉（第一版就差点这么毁文件）
    lines[one(eq(lines, ANCHOR_FN)):one(eq(lines, ANCHOR_FN))] = HELPER + RECS_COMPONENT
    # status 那一行是打包出来的超长三元表达式，只能按前缀认（整行相等永远配不上）
    i_status = one([k for k, l in enumerate(lines) if l.startswith(ANCHOR_STATUS)])
    lines[i_status:i_status] = CONST_LINES
    i = one([k for k in range(len(lines) - 3) if lines[k:k + 4] == [ITEMS_TAIL] + ONSELECT])
    lines[i:i + 4] = ITEMS_NEW + ONSELECT_NEW
    i_row = one(eq(lines, ANCHOR_IN_ROW))
    lines[i_row:i_row] = RECS_ROW
    for anchor, news in DICT:
        i_dict = one(eq(lines, anchor))
        lines[i_dict + 1:i_dict + 1] = news
    return lines


def main():
    raw = io.open(TARGET, "rb").read()
    # 🔴 这份是 CRLF：按 "\n" 切行会让每行末尾挂 \r，锚点永远匹配不上（第一版栽过一次）
    crlf = raw.count(b"\r\n") > 0 and raw.count(b"\r\n") == raw.count(b"\n")
    src = raw.decode("utf-8").replace("\r\n", "\n")
    if "weeksir" in src:
        print("FAIL 目标已带 weeksir 标记 ⇒ 拒绝半补丁状态。")
        print("     重打请先还原：cp '%s' '%s'" % (os.path.basename(PRISTINE_BAK), os.path.basename(TARGET)))
        return 1
    if not os.path.exists(PRISTINE_BAK):
        shutil.copy2(TARGET, PRISTINE_BAK)
        print("已存原版备份:", os.path.basename(PRISTINE_BAK))
    lines = src.split("\n")
    try:
        out = "\n".join(apply(lines))
    except AssertionError as e:
        print("FAIL 锚点不唯一，未落盘:", e)
        return 1
    if out == src:
        print("FAIL 没有任何改动")
        return 1
    tmp = TARGET + ".weeksir-tmp"
    with io.open(tmp, "wb") as f:
        f.write((out.replace("\n", "\r\n") if crlf else out).encode("utf-8"))
    os.replace(tmp, TARGET)
    print("OK  %d → %d 字节" % (len(src), len(out)))
    print("\n=== 官方交付卡补丁 %s 已落盘（下载项 + 推荐行 + 中英文案）===" % REV)
    return 0


if __name__ == "__main__":
    sys.exit(main())

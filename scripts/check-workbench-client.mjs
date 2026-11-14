/**
 * 右栏对账逻辑回归（docs/ADR-workbench-presence.md）
 * 假 ctx + 假 fetch 驱动 client.js 的 apply()，断言：
 *   弹不弹只看【意图】；引擎死了台还在（里面提示怎么起）；新会话一条都不弹；
 *   运维台永不自动弹；页签里不再放导出条（导出走 agent/CLI，2026-10-03 定案）。
 * 跑法：node scripts/check-workbench-client.mjs
 */
import { readFileSync } from "node:fs";

const SRC = "C:/dsh/plugins/ops-gateway/lib/client.js";
const SRC_TXT = readFileSync(SRC, "utf8");
const KEY = "weeksir-pipeline-view", HKEY = "weeksir-hypit-view", MGKEY = "weeksir-model-gateway";
const FKEY = "weeksir-film-view";   // 2026-10-03 另一会话加的 film 看板台 ⇒ 僵尸页签对账要认它
const PRKEY = "weeksir-prompt-reverse-view";   // 2026-10-05 第 6 档：提示词反推（8090）
const MTKEY = "weeksir-motion-transfer-view";  // 2026-10-06 第 7 档：动作迁移（已下线）
const FFKEY = "weeksir-skill-form-view";       // 2026-10-09 输入表单台（8100 托管 /ui/<skill>）
// 闸认得的"会话级自动台"全集。加一档必须同时改这里 + presence 侧，否则 0 号断言先红（数量对不上）。
const AUTO_KEYS = [KEY, HKEY, FKEY, PRKEY, MTKEY, FFKEY].sort();

// 🔴 端口白名单（2026-10-09，docs/PRD-input-form-single-port.md G4/M2）：
//    "UI 口太多"是商业化交付难度的根因，所以这条不写进文档靠人记，写成闸。
//    主口 = 8100；例外只许是【点名的存量冻结线】，且各只许它自己那条线用。
//      8765 = omni 台（产品 2026-10-09 定：omni 整条不动）
//      5179 = hypit 官方 studio（同上）
//    8090 已经在 2026-10-09 从名单里【删掉了】：提示词反推建完字段表、输入搬进 8100 表单台之后，
//    那一档只剩 retired 条目（不含任何端口），右栏代码行里再出现 :8090 就红（0f 钉这条）。
//    名单只许变短：例外端口若没人用而还留在名单里 = 骗自己"已收敛"，0e 会红。
const UI_PORT = "8100";
const EXCEPTION_PORTS = { "8765": ["omni"], "5179": ["hypit"] };
const fails = [];
let total = 0;
const check = (name, got, want) => {
  total++;
  const a = JSON.stringify(got), b = JSON.stringify(want);
  console.log((a === b ? "PASS " : "FAIL ") + name, "=>", a);
  if (a !== b) fails.push([name, a, b]);
};

// 0 号：WORKBENCHES 里"会话级自动台"的数量必须和闸认识的名单一致。
//    为什么要这条：1d/12c 拿一份写死的名单当期望值，加一档忘了改闸 ⇒ 那两条会红得莫名其妙；
//    这条先把"表和闸必须同步改"挑明，红了就说人话。
check("0 WORKBENCHES 的 session 级自动台数 = 闸名单数（加档必改闸）",
      (SRC_TXT.match(/scope:\s*"session",\s*auto:\s*true/g) || []).length, AUTO_KEYS.length);

// 0b 🔴 端口白名单：交互面每多一个口，客户机的交付难度与出错面就多一份 —— 所以这条不许靠人记。
//     判据取"表里每一条 fallback/url 的端口"：8100 无限；8765 只许 omni；5179 只许 hypit。
// 🔴 扫法抽成函数（scanBenches/violations）不是重构洁癖：下面 0g 要用【同一套判据】去跑三种坏法，
//    判据若只能对真文件跑一次，那"它到底还灵不灵"就只剩口头记录（2026-10-09 验收点名的正是这条）。
function scanBenches(txt) {
  const wb = txt.slice(txt.indexOf("const WORKBENCHES"), txt.indexOf("const byKey"));
  return (wb.match(/\{[^{}]*\}/g) || []).map(seg => {
    const m = seg.match(/skill:\s*"?([\w-]+)"?/);
    const ports = [...seg.matchAll(/:(\d{4,5})\b/g)].map(x => x[1]);
    return { skill: m ? m[1] : "?", ports: [...new Set(ports)] };
  });
}
function violations(pb) {
  const bad = pb.filter(b => b.ports.some(pt => {
    if (pt === UI_PORT) return false;
    const owners = EXCEPTION_PORTS[pt];
    return !owners || !owners.includes(b.skill);
  }));
  const used = [...new Set(pb.flatMap(b => b.ports).filter(x => x !== UI_PORT))].sort();
  return {
    bad: bad.map(b => b.skill + ":" + b.ports.join("/")),
    used: used,
    stale: Object.keys(EXCEPTION_PORTS).filter(x => !used.includes(x)),
    form: pb.filter(b => b.skill === "form"),
  };
}
const perBench = scanBenches(SRC_TXT);
const V = violations(perBench);
check("0b 每一档的 UI 端口要么走主口 8100，要么是点名过的存量例外（且只有那条线能用）", V.bad, []);
check("0c 实际用到的例外端口必须逐个都在例外名单里（口数只许减不许增）",
      V.used.filter(x => !(x in EXCEPTION_PORTS)), []);
check("0d 表单档：有且只有一档，且段内【不许硬编任何端口】（url 只能由判定口给，插件不猜第二份）",
      [V.form.length, V.form[0] ? V.form[0].ports : null], [1, []]);
check("0e 例外名单里每个端口都真被某档在用（不留已废弃的例外骗自己「已收敛」）", V.stale, []);

// 0g 🔴 白名单自证：三种坏法必须让【同一套判据】变红。
//     每条都先断言"文本真的被改了"（没改到的变异=恒绿的假对照，比没对照更坏）。
const mutate = (tag, from, to) => {
  if (!SRC_TXT.includes(from)) return { missing: tag + ":锚点不在文件里" };
  const t = SRC_TXT.replace(from, to);
  if (t === SRC_TXT) return { missing: tag + ":替换后文本一字未动" };
  return violations(scanBenches(t));
};
const M1 = mutate("m1", 'fallback: "http://127.0.0.1:8765/"', 'fallback: "http://127.0.0.1:9999/"');
check("0g-m1 存量例外偷偷改指 :9999 ⇒ 0b 红 + 口数变多（0c 也红）",
      [M1.bad || M1.missing, (M1.used || []).includes("9999")], [["omni:9999"], true]);
const M2 = mutate("m2", "{ key: MGKEY,",
  '{ key: "weeksir-ghost-view", skill: "ghost", scope: "session", auto: true, fallback: "http://127.0.0.1:9999/" },\n      { key: MGKEY,');
check("0g-m2 新加一档乱指端口 ⇒ 0b/0c 一起红（白名单不是只防旧档）",
      [(M2.bad || M2.missing).length > 0, (M2.used || []).includes("9999")], [true, true]);
const M3 = mutate("m3", 'fallback: "http://localhost:5179/"', 'fallback: "http://127.0.0.1:8100/hypit"');
check("0g-m3 真把 5179 合进主口了 ⇒ 0e 必须红（例外名单里留着没人用的口=骗自己已收敛）",
      [M3.stale || M3.missing, M3.bad], [["5179"], []]);
// 0f 🔴 8090 彻底退出右栏（2026-10-09）：剥掉注释后的【代码行】里不许再出现 :8090。
//     为什么按代码行而不是整文件：历史写在注释里是有用的（下线原因、判定口仍探那个进程），
//     但代码里一旦出现 :8090，就是有人又把引擎页面摆回右栏 —— 那正是要收敛掉的东西。
const CODE_TXT = SRC_TXT.split("\n").map(l => l.replace(/\/\/.*$/, "")).join("\n");
check("0f 代码行里零命中 :8090（引擎页面不再进右栏，历史只留在注释里）", /:8090\b/.test(CODE_TXT), false);

// 假时钟：presence 有 5 秒缓存，测试里靠推钟跨过去
const realNow = Date.now;
let clock = 0;
Date.now = () => realNow() + clock;
const advance = () => { clock += 10000; };

// ── 假 React：createElement 遇函数组件必须【真递归渲染】，否则 body 断言是假阳性 ──
const hookState = new Map();
let hookKey = "", hookIdx = 0;
const React = {
  createElement: (tag, props, ...kids) => {
    if (typeof tag === "function") {
      hookKey = ((props && props.__wbKey) || tag.name || "?") + "|" + ((props && props.sessionId) || "");
      hookIdx = 0;
      return tag(props);
    }
    return { tag: String(tag), props: props || {}, kids: kids.flat(3).filter(Boolean) };
  },
  useState: init => {
    const slot = hookKey + "#" + (hookIdx++);
    if (!hookState.has(slot)) hookState.set(slot, init);
    return [hookState.get(slot), v => hookState.set(slot, typeof v === "function" ? v(hookState.get(slot)) : v)];
  },
  useEffect: fn => { try { fn(); } catch (e) { console.log("effect err", e.message); } },
  useRef: v => ({ current: v }),
};
const render = (Comp, props) => Comp(props);

// ── 假 fetch：只认判定口和导出口 ────────────────────────────────────────────────
let payload = {};
let SILENT = false;          // true = 判定口没回话（8100 没起 / 抖了）：fetch 直接失败
globalThis.fetch = url => {
  const u = String(url);
  if (u.includes("/api/workbench/presence")) {
    if (SILENT) return Promise.reject(new Error("gateway-down"));
    return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
  }
  if (u.includes("/api/hypit/export")) return Promise.resolve({ ok: true, json: () => Promise.resolve(payload.__export || {}) });
  return Promise.reject(new Error("no-route " + u));
};
const omni = (intent, up = true) => ({ intent: intent, up: up, url: "http://127.0.0.1:8765/?session=s",
                                       reason: up ? "" : "", ready: true });
const hypit = (intent, up = true, run = "productions/hypit-e2e/assemble.svrun") =>
  ({ intent: intent, up: up, port: 5181,   // 🔴 故意不用 5179：iframe 里出现 5179 就说明代码又回落到 fallback 端口了
     run: up ? run : "", reason: up ? "" : "studio-down",
     how_to_start: up ? "" : "hypit studio --run " + run + " --port 5179", run_verified: up });
// 第 6 档（2026-10-09 工作台【下线】）：输入搬进 8100 表单台 ⇒ 判定口把这一档顶成 intent=False（reason=form-is-input）。
// url 里带 8090 是【线上真实形状】（判定口仍真探那个引擎进程，地址只当信息给 agent），
// 台里不许用它 —— 7b-m 就是钉这一点的：出现 :8090 就是有人又把引擎页面摆回右栏。
const reverse = (intent, up = true) => ({ intent, up, alive: up,
  url: "http://127.0.0.1:8090/prompt-reverse", task: "反推爆款01",
  how_to_start: up ? "" : "在 backend/ 目录下起 uvicorn 8090", reason: intent ? "form-is-input" : "no-intent" });
// 表单台（2026-10-09 新增档）：形状是 2026-10-09 从线上 presence 抄下来的真实回包，不是编的。
//   有意图 = 这条会话登记的档【配了字段表】；url 由判定口给（它才知道是哪个技能、哪条会话）。
const form = (intent, skill = "one-shot-video") => ({ intent, up: true, alive: true,
  source: intent ? "explicit" : "", skill: intent ? skill : "", intent_skill: intent ? skill : "",
  task: intent ? "鼻贴一句话出片" : "",
  url: intent ? ("http://127.0.0.1:8100/ui/" + skill + "?session=s") : "",
  reason: intent ? "" : "no-intent" });
// 第 7 档（2026-10-08 工作台【下线】）：presence 的 intent 恒 False（reason=workbench-retired）。
// intent 参数只用于"判定口意外回 true"的变异对照 —— client 侧 retired 双保险必须顶住。
const motion = (intent = false) => ({ intent, up: false, alive: false,
  reason: intent ? "" : "workbench-retired" });

// ── 装载插件 ────────────────────────────────────────────────────────────────────
let loaded = null;
globalThis.window = { __ModuleLoader__: { load: m => { loaded = m; } } };
new Function("window", "require", readFileSync(SRC, "utf8"))(globalThis.window, () => React);
const mod = loaded.factory(() => React);
const captured = {};
function makeCtx(sidRef, titles = {}) {
  const calls = { open: [], close: [], tick: null, ticks: [], subs: [], renames: [] };
  const ctx = {
    effect: fn => { fn(); return () => { } },
    slots: {
      inject: (name, fn) => { try { fn(); } catch (e) { } },
      register: (meta, Comp) => { captured[meta.key + "|" + (String(meta.name).endsWith(".title") ? "title" : "body")] = Comp; },
    },
    sidebarRightTabs: { register: () => { } },
    sidebarRight: {
      openTab: (key, options) => calls.open.push([key, !!(options && options.revealIfOpened)]),
      closeIn: (sessionId, tabId) => calls.close.push([sessionId, tabId]),
    },
    // byId 供标题前缀读现标题；binding().session.rename 记录调用（真内核里它写 user 源标题）
    sessions: {
      list: { getSnapshot: () => ({ current: sidRef.v, byId: { [sidRef.v]: { title: titles[sidRef.v] || "" } } }),
              subscribe: fn => calls.subs.push(fn) },
      binding: id => ({ session: { rename: t => { calls.renames.push([id, t]); return Promise.resolve({}); } } }),
    },
    // 真源的形状：定时器走 ctx.timer.setInterval（inject 名单里有 timer），ctx.timer 不在时退回全局 setInterval。
    // 老闸把假 ctx 写成 ctx.setInterval（那是被 guard 拒的读法），改完真源后这里就接不到回调 ⇒ calls.tick=null ⇒ 崩。
    timer: { setInterval: fn => { calls.ticks.push(fn); if (!calls.tick) calls.tick = fn; return 1; } },
  };
  return { ctx, calls };
}
const norm = id => String(id).replace(/^dsh-resource:\/\/(weeksir\/)?/, "");
// 只看"关了哪些台"，不数 tryClose 的候选 id 条数
const closedKeys = (calls, sid, from = 0) =>
  [...new Set(calls.close.slice(from).filter(c => c[0] === sid).map(c => norm(c[1])))].sort();
const settle = () => new Promise(r => setTimeout(r, 0));
const settle5 = () => new Promise(r => setTimeout(r, 5));
// 页签体要渲染两次：第一次触发 effect 拿判定，第二次才看得到结果
const bodyJson = async (key, sid) => {
  render(captured[key + "|body"], { sessionId: sid });
  await settle5();
  return JSON.stringify(render(captured[key + "|body"], { sessionId: sid }));
};
const run = async (calls, n = 1) => { for (let i = 0; i < n; i++) { advance(); calls.tick(); await settle(); } };

(async () => {
  check("inject 声明含 timer/sessions（否则 setInterval/读会话被 guard 拒）",
    ["timer", "sessions"].every(k => mod.inject.includes(k)), true);

  // 1) 新会话：两台都无意图 ⇒ 一条都不弹
  let sid = { v: "session-NEW" };
  payload = { omni: omni(false, false), hypit: hypit(false, false) };
  const a = makeCtx(sid);
  mod.apply(a.ctx);
  await run(a.calls, 2);
  check("1 新会话不猜意图：一条页签都不弹", a.calls.open.map(o => o[0]), []);
  // 1d 🔴 首次判定为"无意图"也要去关 —— 清掉旧版本（hypit 曾弹进每条会话）留下的僵尸页签。
  //    这条就是用户报"无关会话里出现这个提示"的根因：只等下降沿的话，僵尸页签永远留着。
  check("1d 首次无意图也会去关僵尸页签（会话级自动台全试一遍）", closedKeys(a.calls, sid.v), AUTO_KEYS);

  // 2) 说了做 omni ⇒ 只弹流水线
  payload = { omni: omni(true), hypit: hypit(false, false) };
  await run(a.calls);
  check("2 有 omni 意图 ⇒ 只弹流水线", a.calls.open.map(o => o[0]), [KEY]);

  // 3) 又去跑 hypit ⇒ 两个台都在（同会话跨流水线合法）
  payload = { omni: omni(true), hypit: hypit(true) };
  await run(a.calls);
  check("3 hypit 意图出现 ⇒ 补弹一个", a.calls.open.map(o => o[0]), [KEY, HKEY]);

  // 4) 🔴 关键新语义：studio 死了，台不该消失，只是里面换成"引擎没在跑"
  const openedBefore = a.calls.open.length;
  payload = { omni: omni(true), hypit: hypit(true, false) };
  await run(a.calls, 2);
  check("4 引擎死了不关台（弹的次数不变）", a.calls.open.length, openedBefore);
  // 🔴 2026-10-03 语义更正：查看器没起 ≠ 引擎没在跑，且不再把命令行印给人
  const g4 = await bodyJson(HKEY, sid.v);
  check("4b 查看器没起：说「studio 查看器没起」，不再谎报「引擎没在跑」",
        [g4.includes("studio 查看器没起"), g4.includes("引擎没在跑")], [true, false]);
  check("4c 也不再甩 hypit studio --run 命令行（起查看器是 agent 的活）", g4.includes("hypit studio --run"), false);

  // 5) 意图撤销（release 后）⇒ 只关它自己的台，有意图的那个不动
  const mark5 = a.calls.close.length;
  payload = { omni: omni(true), hypit: hypit(false, false) };
  await run(a.calls);
  check("5 意图没了只关它自己的台（有意图的那个不动）", closedKeys(a.calls, sid.v, mark5), [HKEY]);

  // 6) 重新有意图会再弹（已重新上膛）
  payload = { omni: omni(true), hypit: hypit(true) };
  await run(a.calls);
  check("6 重新有意图会再弹（已重新上膛）", a.calls.open.filter(o => o[0] === HKEY).length, 2);

  // 7) 持续为真不重复弹
  await run(a.calls, 3);
  check("7 持续为真不重复弹", a.calls.open.filter(o => o[0] === HKEY).length, 2);

  // 7b) 第 6 档（2026-10-09 工作台【下线】）：prompt-reverse 的输入搬进 8100 表单台 ⇒ 引擎台一个都不弹。
  //     判定口那边由 form-is-input 顶掉 intent；client 这边 retired 是双保险（7b-m 变异对照钉它）。
  const openBefore7b = a.calls.open.length;
  payload["prompt-reverse"] = reverse(false);        // 真实形状（线上实测）：intent=False + reason=form-is-input
  payload["form"] = form(true, "prompt-reverse");
  await run(a.calls);
  check("7b 反推档不再自动弹引擎台，弹的是表单台（只补一档）",
        [a.calls.open.length - openBefore7b, a.calls.open[a.calls.open.length - 1][0]], [1, FFKEY]);
  // 变异对照：判定口意外回 intent:true（顶替逻辑坏了/表单台没登记）⇒ retired 必须仍然不弹
  payload["prompt-reverse"] = reverse(true);
  await run(a.calls, 2);
  check("7b-m 判定口意外回 intent:true ⇒ PRKEY 的 retired 双保险顶住，不弹也不摆 8090 页面",
        [a.calls.open.length - openBefore7b, (await bodyJson(PRKEY, sid.v)).includes(":8090")], [1, false]);

  // 7d) 第 7 档（2026-10-08 工作台【下线】）：登记 motion-transfer ⇒ 一个台都不弹；
  //     僵尸页签清理仍覆盖 MTKEY（1d 那条钉着：旧版本弹过的台要能被关掉）。
  const openBefore7d = a.calls.open.length;
  payload["motion-transfer"] = motion(false);       // 真实形状：intent 恒 False + workbench-retired
  await run(a.calls);
  check("7d 登记 motion-transfer 也不弹台（第 7 档工作台已下线）", a.calls.open.length - openBefore7d, 0);
  // 变异对照：判定口意外回 intent:true —— client 的 retired 双保险必须顶住，且不摆死页面
  payload["motion-transfer"] = motion(true);
  await run(a.calls, 2);
  const g7d = await bodyJson(MTKEY, sid.v);
  check("7d2 判定口意外回 intent:true 也不弹（retired 双保险），页面只说已下线、不指 8091",
        [a.calls.open.length - openBefore7d, g7d.includes("已下线"), g7d.includes("8091")], [0, true, false]);
  delete payload["motion-transfer"];

  // 7e) 表单台行为（2026-10-09 补：这一档此前只钉过"表里有它 + 不硬编端口"，没钉过它到底画什么）。
  //     🔴 地址必须来自判定口：插件一旦自己拼 "/ui/<skill>"，就是第二份"谁该出表"的判据。
  payload["form"] = form(true, "one-shot-video");
  await run(a.calls, 2);
  const g7e = await bodyJson(FFKEY, sid.v);
  check("7e 有表单意图 ⇒ iframe 开判定口给的 8100 /ui/<skill>（地址不在插件里拼第二份）",
        [g7e.includes("127.0.0.1:8100/ui/one-shot-video?session="), g7e.includes('"tag":"iframe"')], [true, true]);
  // 判定口说"登记的档没配字段表" ⇒ 关台，不多摆一张空表（和"新会话零 UI"是同一条判据）
  const closeMark7e = a.calls.close.length;
  payload["form"] = { intent: false, up: true, alive: true, reason: "intent-has-no-form" };
  await run(a.calls, 2);
  check("7e2 登记的档没配字段表 ⇒ 表单台关掉（不画一张空表占右栏）",
        closedKeys(a.calls, sid.v, closeMark7e), [FFKEY]);
  // 无意图时不给「我就是要看」：这一档没有兜底页，硬显 = 塞一张不知道属于谁的表。
  // 🔴 反向对照 = 上面 10b（hypit 没意图时按钮【必须】在）—— 两条一起才证明判的是"有没有兜底页"，不是恒真。
  const g7e3 = await bodyJson(FFKEY, sid.v);
  check("7e3 无意图的表单台只说「先点技能」，不给「我就是要看」、也不画 iframe",
        [g7e3.includes("我就是要看"), g7e3.includes('"tag":"iframe"'), g7e3.includes("intent-has-no-form")],
        [false, false, true]);
  delete payload["prompt-reverse"];
  delete payload["form"];

  // 8) 运维台永不自动弹，但手动开要出台
  const b = makeCtx({ v: "session-OPS" });
  payload = { omni: omni(true), hypit: hypit(true) };
  mod.apply(b.ctx);
  await run(b.calls, 2);
  check("8 运维台不自动弹", b.calls.open.map(o => o[0]), [KEY, HKEY]);
  check("8b 手动开运维台照常出控制台",
    (await bodyJson(MGKEY, "session-OPS")).includes("127.0.0.1:8100"), true);

  // 9) 切走再切回：状态按 (会话,页签) 记，所以切回来后意图没了仍能等到下降沿把台关掉
  const c = makeCtx(sid);
  payload = { omni: omni(true), hypit: hypit(true) };
  mod.apply(c.ctx);
  await run(c.calls);
  sid.v = "session-OTHER";
  payload = { omni: omni(false, false), hypit: hypit(false, false) };
  c.calls.subs.forEach(fn => fn());
  await run(c.calls);
  // 切到一个新会话：它自己的台会被"首次无意图"清一遍（无害 no-op），但不能碰老会话的台
  check("9 切会话只动本会话的页签（不碰刚那个老会话）", [...new Set(c.calls.close.map(x => x[0]))],
        ["session-NEW","session-OTHER"]);
  sid.v = "session-NEW";
  payload = { omni: omni(true), hypit: hypit(false, false) };
  c.calls.subs.forEach(fn => fn());
  await run(c.calls);
  check("9b 切回老会话、意图已撤 => 关它自己的台（不碰别的会话）",
        closedKeys(c.calls, "session-NEW", c.calls.close.filter(x => x[0] === "session-OTHER").length)
          .filter(k => k === HKEY), [HKEY]);

  // 10) body 三态：无意图=门禁、有意图+活=iframe、有意图+死=起法
  payload = { omni: omni(false, false), hypit: hypit(false, false) };
  const cold = await bodyJson(HKEY, "session-X");
  check("10 无意图时不渲染 iframe", cold.includes('"tag":"iframe"'), false);
  check("10b 但给了「直接显示」出口", cold.includes("我就是要看"), true);
  payload = { omni: omni(true), hypit: hypit(true) };
  check("10c 有意图+活着 ⇒ 出 iframe 且端口来自判定口",
    (await bodyJson(HKEY, "session-Y")).includes("localhost:5181"), true);

  const findBtns = (node, out = []) => {        // 被我删掉的旧组 11 里定义的，后面 16/18 还要用
    if (!node || typeof node !== "object") return out;
    if (node.tag === "button") out.push(node);
    for (const k of (node.kids || [])) findBtns(k, out);
    return out;
  };

  // 11) 🔴 定案变更（2026-10-03）：页签里不放导出条了 —— 界面归官方 studio/进度台，
  //     导出仍走服务端闸门（/api/hypit/export 与那 29 条断言不动，触发者是 agent/CLI）。
  payload = { omni: omni(false, false), hypit: hypit(true, true) };     // 不用 PROG：它在后面才定义
  render(captured[HKEY + "|body"], { sessionId: "session-Z" });
  await settle5();
  const live = JSON.stringify(render(captured[HKEY + "|body"], { sessionId: "session-Z" }));
  check("11 页签里不再出现导出条（输入框/按钮都没有）",
        [live.includes("导出成片"), live.includes("任务目录"), live.includes("ExportBar")], [false, false, false]);
  check("11a 组件本体也从包里删掉了（不是藏起来）", SRC_TXT.includes("function ExportBar"), false);

  // 12~13b) 🔴 判定口哑掉（8100 没起/抖了）≠ 这条会话没意图：短哑不关台，长哑才认输，回来能再弹
  const d = makeCtx({ v: "session-FLAP" });
  payload = { omni: omni(true), hypit: hypit(true) };
  mod.apply(d.ctx);
  await run(d.calls);
  check("12 有意图 ⇒ 两个台都弹了（后面的哑掉测试的基础）", d.calls.open.map(o => o[0]), [KEY, HKEY]);
  const closes12 = d.calls.close.length;
  SILENT = true;
  await run(d.calls, 3);                       // 30 秒没回话
  check("12b 判定口哑 30 秒：一个台都不关（旧版在这会把全部台关掉再弹回来=闪断）",
        d.calls.close.length, closes12);
  await run(d.calls, 9);                       // 累计 120 秒 > 90 秒容忍窗
  check("12c 哑过 90 秒 ⇒ 认输关掉（fail-closed，不是永远挂着）",
        closedKeys(d.calls, "session-FLAP", closes12), AUTO_KEYS);
  SILENT = false;
  await run(d.calls);
  check("13b 判定口回来 ⇒ 重新弹（上膛没坏）", d.calls.open.length, 4);

  // 13) 冷启动就哑（一次判定都没拿到）⇒ 不许谎报"本会话不属于这个台"，要说"判定口没回话"
  SILENT = true;
  const coldSilent = await bodyJson(HKEY, "session-COLD-SILENT");
  check("13 冷启动哑：说判定口没回话", coldSilent.includes("判定口没回话"), true);
  check("13d 冷启动哑：不谎报成「本会话没意图」的门禁", coldSilent.includes("不属于当前会话"), false);
  // 14) 哑但手上还有旧判定 ⇒ 沿用旧判定（继续出台，不换成门禁）
  advance();
  const keepOld = await bodyJson(HKEY, "session-Z");
  check("14 哑时沿用上一次判定：还是出 iframe，不切成门禁", keepOld.includes('"tag":"iframe"'), true);
  SILENT = false;

  // 15) 🔴 生成工程的默认视图 = 进度台：studio 没起也出内容，不给人命令行提示
  const PROG = { known: true, source: "gateway-jobs", projects: ["productions/nose-cream"],
                 summary: { total: 2, ok: 1, failed: 1, running: 0, queued: 0, credits: 50 },
                 segments: [{ id: "aaaa1111bbbb2222", model: "seedance-2-mini", state: "succeeded",
                              label: "request-video-generate", credits: 50, seconds: 312, ago: 900, error: "", upstream: "",
                              output: "http://localhost:8100/api/hypit/assets/aaaa1111bbbb2222.mp4",
                              clip: "productions/nose-cream/clips/seg1.mp4" },
                            { id: "cccc3333dddd4444", model: "seedance-2-mini", state: "failed",
                              label: "request-audio-render", credits: 0, seconds: 1800, ago: 120,
                              error: "轮询超时 1800s（上游任务 d909fa86e1bf43f484c2e20aa106bd34，可据此查）",
                              upstream: "d909fa86e1bf43f484c2e20aa106bd34" }] };
  payload = { omni: omni(false, false), hypit: Object.assign(hypit(true, false), { progress: PROG }) };
  const panel = await bodyJson(HKEY, "session-PROG");
  check("15 studio 没起 + 有生成腿 ⇒ 出进度台", panel.includes("生成进度"), true);
  check("15b 汇总/扣分/段名/上游任务号都在",
        [panel.includes("已扣 50 分"), panel.includes("request-audio-render"), panel.includes("d909fa86e1bf")],
        [true, true, true]);
  check("15c 不再把命令行提示丢给人", panel.includes("hypit studio --run"), false);
  check("15d 进度态不渲染 iframe（生成工程的 studio 页会 500）", panel.includes('"tag":"iframe"'), false);
  // 16) 有意图 + 引擎活着 ⇒ 进度台仍优先，但给"打开导演台"的出口；点了才出 iframe
  payload = { omni: omni(false, false), hypit: Object.assign(hypit(true, true), { progress: PROG }) };
  render(captured[HKEY + "|body"], { sessionId: "session-FRAME" });
  await settle5();
  const withPanel = render(captured[HKEY + "|body"], { sessionId: "session-FRAME" });
  // 🔴 顺序更正：studio 活着 ⇒ 默认就是官方界面（任务页在左栏第三个 tab），进度台退成可切换视图
  check("16 studio 活着 ⇒ 默认出官方界面（不是自绘视图抢位）",
        [JSON.stringify(withPanel).includes('"tag":"iframe"'), JSON.stringify(withPanel).includes("看精简进度")], [true, true]);
  // 16a 🔴 iframe 必须开 presence 报的那个端口。曾经的坑：stateOf 把 url 预填成 fallback(5179)，
  //      frame() 又优先用 url ⇒ 右栏永远开 5179 上那台【别的工程】（用户："视频牛头不对马嘴"）
  check("16a iframe 开的是判定口报的端口，不是 5179 兜底",
        [JSON.stringify(withPanel).includes("localhost:5181"), JSON.stringify(withPanel).includes("localhost:5179")], [true, false]);
  const btn = findBtns(withPanel).find(x => JSON.stringify(x.kids).includes("看精简进度"));
  btn.props.onClick();
  const afterFrame = JSON.stringify(render(captured[HKEY + "|body"], { sessionId: "session-FRAME" }));
  check("16b 点「看精简进度」才切到自绘面板（iframe 让位）",
        [afterFrame.includes('"tag":"iframe"'), afterFrame.includes("网关账本")], [false, true]);
  // 17) 🔴 定案变更（2026-10-03 用户：「不能等 .svml 出现才触发 studio，精简版可以切换，挺好的」）：
  //     没腿也直接给精简进度台 + 一句实话说明，不再只印一张文字卡。
  payload = { omni: omni(false, false), hypit: hypit(true, false) };
  const nog = await bodyJson(HKEY, "session-NO-LEGS");
  check("17 账本里没腿也出精简进度台（不是一张死胡同文字卡）",
        [nog.includes("生成进度"), nog.includes("本会话还没有生成腿"), nog.includes('"tag":"iframe"')],
        [true, true, false]);
  check("17a 但要说清为什么只有进度：查看器没起、不影响生成",
        [nog.includes("查看器没起，不影响生成"), nog.includes("hypit studio --run")], [true, false]);
  // 17b) 没腿但查看器活着 ⇒ 官方界面照常是默认，切换口不许依赖"有腿"
  payload = { omni: omni(false, false), hypit: Object.assign(hypit(true, true), { progress: null }) };
  const up0 = await bodyJson(HKEY, "session-UP-NOLEGS");
  check("17b 查看器活着+零腿 ⇒ iframe 与「看精简进度」同时给",
        [up0.includes('"tag":"iframe"'), up0.includes("看精简进度")], [true, true]);
  // 17c) 工程还没落地（no-project）⇒ 面板顶部要说出 .svrun 这件事，且不承诺用户去敲命令
  payload = { omni: omni(false, false),
              hypit: Object.assign(hypit(true, false), { state: "no-project", reason: "no-project" }) };
  const nop = await bodyJson(HKEY, "session-NO-PROJ");
  check("17c no-project 也进面板，话说清「还没有 .svrun、会自动起」",
        [nop.includes("生成进度"), nop.includes(".svrun"), nop.includes("你不用敲任何命令")], [true, true, true]);
  // 18) 🔴 有在途腿 + studio 没起：这一档必须是"正在生成"，不是"引擎没在跑"、也不是命令行
  const LIVE = JSON.parse(JSON.stringify(PROG));
  LIVE.summary = { total: 3, ok: 1, failed: 1, running: 1, queued: 0, credits: 50 };
  LIVE.segments.unshift({ id: "eeee5555ffff6666", model: "seedance-2-mini", state: "running",
                          label: "generate", credits: 0, seconds: 214, ago: 214, error: "", upstream: "" });
  payload = { omni: omni(false, false), hypit: Object.assign(hypit(true, false), { state: "generating",
                active_legs: 1, progress: LIVE }) };
  const gen = await bodyJson(HKEY, "session-GENERATING");
  check("18 有在途腿 ⇒ 说「正在生成」", [gen.includes("正在生成"), gen.includes("引擎在工作")], [true, true]);
  check("18b 这一档绝不出现「引擎没在跑」，也不出现命令行",
        [gen.includes("引擎没在跑"), gen.includes("hypit studio --run")], [false, false]);
  check("18c running 段也在列表里", gen.includes("214s"), true);

// ── 成片回传与"官方界面优先"（2026-10-03）──
  // 19) 🔴 看片改成"就地播"（用户 2026-10-03：不要跳转）：按钮展开 <video>，页签里不许再有甩出 DSH 的链接
  payload = { omni: omni(false, false), hypit: Object.assign(hypit(true, false), { progress: PROG }) };
  render(captured[HKEY + "|body"], { sessionId: "session-PLAY" });
  await settle5();
  const pv = render(captured[HKEY + "|body"], { sessionId: "session-PLAY" });
  const p0 = JSON.stringify(pv);
  const playBtn = findBtns(pv).find(x => JSON.stringify(x.kids).includes("看片"));
  check("19 成功段给的是「看片」按钮（就地播），不是跳出去的链接",
        [!!playBtn, p0.includes('"tag":"video"'), /target:\s*"\_blank"/.test(p0)], [true, false, false]);
  playBtn.props.onClick();
  const t1 = render(captured[HKEY + "|body"], { sessionId: "session-PLAY" });
  const p1 = JSON.stringify(t1);
  check("19b 点一下就在面板里播：出现 <video> 且 src 是账本里那条 8100 地址",
        [p1.includes('"tag":"video"'), p1.includes("http://localhost:8100/api/hypit/assets/aaaa1111bbbb2222.mp4"),
         p1.includes("preload")], [true, true, true]);
  check("19c 回传路径也显示出来（导演台看得见片段的原因）", p1.includes("clips/seg1.mp4"), true);
  const collapsed = findBtns(t1).find(x => JSON.stringify(x.kids).includes("收起"));
  check("19c2 展开后按钮文案变「收起」", !!collapsed, true);
  collapsed.props.onClick();
  check("19d 再点收起，播放器不占地方",
        JSON.stringify(render(captured[HKEY + "|body"], { sessionId: "session-PLAY" })).includes('"tag":"video"'), false);
check("19z 查看器起来的上升沿把视图交还官方 studio（自绘台不永久挡官方界面）",
      SRC_TXT.includes("!p.st.up && p.view === \"panel\""), true);

// ── 会话标题前缀（2026-10-05 用户："确定了意图就在会话名前加 [超级复刻]/[omni出片]"）──
  // 最后一条 setInterval 是 titlePrefixTick（第一条是 reconcile）；推钟是为了跨过 presence 的 5 秒缓存
  const tickTitle = async (calls, n = 1) => {
    for (let i = 0; i < n; i++) { advance(); calls.ticks[calls.ticks.length - 1](); await settle(); }
  };
  const bytesOf = s => new TextEncoder().encode(s).length;

  {
    const ref = { v: "session-TITLE-1" };
    const t = { [ref.v]: "鼻贴成片方案" };
    payload = { omni: omni(true), hypit: hypit(false, false) };
    const e = makeCtx(ref, t);
    mod.apply(e.ctx);
    await settle();
    check("20 有 omni 意图 ⇒ 会话名加 [omni出片] 前缀（只动当前会话）", e.calls.renames, [[ref.v, "[omni出片] 鼻贴成片方案"]]);
    t[ref.v] = e.calls.renames[0][1];
    await tickTitle(e.calls, 2);
    check("20b 已经带前缀 ⇒ 不再 rename（不每 30 秒刷一次标题）", e.calls.renames.length, 1);
    payload = { omni: omni(false, false), hypit: hypit(true) };
    t[ref.v] = "[omni出片] 鼻贴成片方案";
    await tickTitle(e.calls);
    check("20c 换流水线 ⇒ 剥掉旧前缀再挂新的（不叠罗汉）", e.calls.renames[1], [ref.v, "[hypit] 鼻贴成片方案"]);
    check("20d 标题前缀是一条独立的 setInterval，不只在 apply 时跑一次",
          [e.calls.ticks.length, SRC_TXT.includes("every(titlePrefixTick, 30000)")], [2, true]);
  check("20d2 定时器只走 ctx.timer.setInterval，并且带『读不到就退回全局』的守卫（裸 ctx.setInterval 会被 guard 抛）",
    [SRC_TXT.includes("typeof ctx.timer.setInterval === \"function\""), SRC_TXT.includes("ctx.setInterval(")],
    [true, false]);
  }

  {   // 内核 maxTitleBytes=80，超了直接拒 rename ⇒ 必须自己按 UTF-8 字节截，且不能切出半个汉字
    const ref = { v: "session-TITLE-LONG" };
    const t = { [ref.v]: "电".repeat(40) };
    payload = { omni: omni(true), hypit: hypit(false, false) };
    const e = makeCtx(ref, t);
    mod.apply(e.ctx);
    await settle();
    const given = (e.calls.renames[0] || [null, ""])[1];
    check("21 长标题截到 80 字节以内，且不切半个字（前缀 13 字节 + 22 个汉字 = 79）",
          [given, bytesOf(given) <= 80], ["[omni出片] " + "电".repeat(22), true]);
  }

  {   // 无意图 / 读不到会话 / 还没标题：三种都不许乱改
    const refN = { v: "session-TITLE-NONE" };
    payload = { omni: omni(false, false), hypit: hypit(false, false) };
    const eN = makeCtx(refN, { [refN.v]: "随便一条闲聊" });
    mod.apply(eN.ctx);
    await settle();
    check("22 没登记意图 ⇒ 一个标题都不改（和「新会话零 UI」是同一条铁律）", eN.calls.renames, []);

    const ref0 = { v: "" };
    payload = { omni: omni(true), hypit: hypit(false, false) };
    const e0 = makeCtx(ref0, {});
    mod.apply(e0.ctx);
    await settle();
    check("22c 读不到当前会话 id ⇒ 不动任何标题（绝不猜一条会话去改）", e0.calls.renames, []);

    const refB = { v: "session-TITLE-BLANK" };
    const eB = makeCtx(refB, {});
    mod.apply(eB.ctx);
    await settle();
    check("23 内核还没给标题 ⇒ 拿会话 id 兜底，不 rename 成光杆前缀", eB.calls.renames,
          [[refB.v, "[omni出片] session-TITL"]]);
  }

  console.log("\n=== %d 条断言，失败 %d ===", total, fails.length);
  for (const [n, g, w] of fails) console.log("  FAIL", n, "got", g, "want", w);
  process.exit(fails.length ? 1 : 0);
})();

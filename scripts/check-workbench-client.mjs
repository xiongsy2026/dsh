/**
 * 右栏对账逻辑回归（docs/ADR-workbench-presence.md）
 * 假 ctx + 假 fetch 驱动 client.js 的 apply()，断言：
 *   弹不弹只看【意图】；引擎死了台还在（里面提示怎么起）；新会话一条都不弹；
 *   运维台永不自动弹；导出条只在有 run 时可点。
 * 跑法：node scripts/check-workbench-client.mjs
 */
import { readFileSync } from "node:fs";

const SRC = "C:/dsh/plugins/ops-gateway/lib/client.js";
const KEY = "weeksir-pipeline-view", HKEY = "weeksir-hypit-view", MGKEY = "weeksir-model-gateway";
const fails = [];
let total = 0;
const check = (name, got, want) => {
  total++;
  const a = JSON.stringify(got), b = JSON.stringify(want);
  console.log((a === b ? "PASS " : "FAIL ") + name, "=>", a);
  if (a !== b) fails.push([name, a, b]);
};

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
globalThis.fetch = url => {
  const u = String(url);
  if (u.includes("/api/workbench/presence")) return Promise.resolve({ ok: true, json: () => Promise.resolve(payload) });
  if (u.includes("/api/hypit/export")) return Promise.resolve({ ok: true, json: () => Promise.resolve(payload.__export || {}) });
  return Promise.reject(new Error("no-route " + u));
};
const omni = (intent, up = true) => ({ intent: intent, up: up, url: "http://127.0.0.1:8765/?session=s",
                                       reason: up ? "" : "", ready: true });
const hypit = (intent, up = true, run = "productions/hypit-e2e/assemble.svrun") =>
  ({ intent: intent, up: up, port: 5179, run: up ? run : "", reason: up ? "" : "studio-down",
     how_to_start: up ? "" : "hypit studio --run " + run + " --port 5179", run_verified: up });

// ── 装载插件 ────────────────────────────────────────────────────────────────────
let loaded = null;
globalThis.window = { __ModuleLoader__: { load: m => { loaded = m; } } };
new Function("window", "require", readFileSync(SRC, "utf8"))(globalThis.window, () => React);
const mod = loaded.factory(() => React);
const captured = {};
function makeCtx(sidRef) {
  const calls = { open: [], close: [], tick: null, subs: [] };
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
    sessions: { list: { getSnapshot: () => ({ current: sidRef.v }), subscribe: fn => calls.subs.push(fn) } },
    setInterval: fn => { calls.tick = fn; return 1; },
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
  check("1d 首次无意图也会去关僵尸页签（本会话两个台都试）", closedKeys(a.calls, sid.v), [HKEY, KEY].sort());

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
  check("4b 台里显示引擎没在跑 + 怎么起",
    (await bodyJson(HKEY, sid.v)).includes("hypit studio --run"), true);

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
        ["session-OTHER"]);
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
    (await bodyJson(HKEY, "session-Y")).includes("localhost:5179"), true);

  // 11) 导出条：有 run 才能点；studio 死了不给 run ⇒ 按钮禁用
  const findBtns = (node, out = []) => {
    if (!node || typeof node !== "object") return out;
    if (node.tag === "button") out.push(node);
    for (const k of (node.kids || [])) findBtns(k, out);
    return out;
  };
  payload = { omni: omni(false, false), hypit: hypit(true) };
  render(captured[HKEY + "|body"], { sessionId: "session-Z" });
  await settle5();
  const live = render(captured[HKEY + "|body"], { sessionId: "session-Z" });
  const bar = findBtns(live).find(x => JSON.stringify(x.kids).includes("导出成片"));
  check("11 有 hypit 意图时给导出条", !!bar, true);
  check("11a 有 run ⇒ 按钮可点", bar.props.disabled, false);
  // 11b studio 死了走"引擎没在跑"分支 —— 那里根本没有导出条（比"禁用"更强）
  payload = { omni: omni(false, false), hypit: hypit(true, false) };
  render(captured[HKEY + "|body"], { sessionId: "session-W" });
  await settle5();
  const dead = JSON.stringify(render(captured[HKEY + "|body"], { sessionId: "session-W" }));
  check("11b 引擎死了：不给导出条", dead.includes("导出成片"), false);
  check("11c 但台还在，并告诉你怎么起", dead.includes("hypit studio --run"), true);

  console.log("\n=== %d 条断言，失败 %d ===", total, fails.length);
  for (const [n, g, w] of fails) console.log("  FAIL", n, "got", g, "want", w);
  process.exit(fails.length ? 1 : 0);
})();

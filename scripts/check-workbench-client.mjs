/**
 * 右栏工作台对账逻辑回归（#32）：用假 ctx + 假 fetch 驱动 client.js 的 apply()，
 * 断言"该弹才弹、不该弹就关、换会话重新判定、没有启动即开的旁路"。
 * 跑法：node scripts/check-workbench-client.mjs
 */
import { readFileSync } from "node:fs";

const SRC = "C:/dsh/plugins/ops-gateway/lib/client.js";
const fails = [];
let total = 0;
const check = (name, got, want) => {
  total++;
  const a = JSON.stringify(got), b = JSON.stringify(want);
  console.log((a === b ? "PASS " : "FAIL ") + name, "=>", a);
  if (a !== b) fails.push([name, a, b]);
};

// ── 假 React（够跑 useState/useEffect 的初值分支）────────────────────────────────
const React = {
  createElement: (tag, props, ...kids) => ({ tag: tag && tag.name ? tag.name : String(tag), props: props || {}, kids: kids.flat(3).filter(Boolean) }),
  useState: init => [init, () => { }],
  useEffect: fn => { try { fn(); } catch (e) { } },
};

// ── 假 fetch：按 URL 前缀返回可编程结果 ─────────────────────────────────────────
let routes = {};
globalThis.fetch = url => {
  const hit = Object.keys(routes).find(k => String(url).includes(k));
  if (!hit) return Promise.reject(new Error("no-route"));
  const v = routes[hit];
  return Promise.resolve(typeof v === "function" ? v() : { ok: true, json: () => Promise.resolve(v) });
};

// ── 加载插件模块，拿到 apply/inject ────────────────────────────────────────────
let loaded = null;
globalThis.window = { __ModuleLoader__: { load: m => { loaded = m; } } };
new Function("window", "require", readFileSync(SRC, "utf8"))(globalThis.window, n => {
  if (n === "react") return React;
  throw new Error("unexpected require " + n);
});
const mod = loaded.factory(n => (n === "react" ? React : (() => { throw new Error(n); })()));

check("inject 声明含 timer/sessions（否则 setInterval/会话读取会被 guard 拒）",
  ["slots", "sidebarRightTabs", "sidebarRight", "timer", "sessions"].every(k => mod.inject.includes(k)), true);

// ── 假 ctx（captured 收集 slots.register 进来的组件，供 body 断言用）─────────────
const captured = {};
function makeCtx(sid) {
  const calls = { open: [], close: [], tick: null, subs: [] };
  const ctx = {
    effect: fn => { fn(); return () => { } },
    slots: {
      inject: (name, fn) => { try { fn(); } catch (e) { } },   // 真 DSH 会调用它，注册回调里才 register
      register: (meta, Comp) => { captured[meta.key + "|" + (String(meta.name).endsWith(".title") ? "title" : "body")] = Comp; },
    },
    sidebarRightTabs: { register: () => { } },
    sidebarRight: {
      openTab: (key, opts) => calls.open.push([key, !!(opts && opts.revealIfOpened)]),
      closeIn: (sessionId, tabId) => calls.close.push([sessionId, tabId]),
    },
    sessions: { list: { getSnapshot: () => ({ current: sid.v }), subscribe: fn => calls.subs.push(fn) } },
    setInterval: fn => { calls.tick = fn; return 1; },
  };
  return { ctx, calls };
}
const KEY = "weeksir-pipeline-view", HKEY = "weeksir-hypit-view", MGKEY = "weeksir-model-gateway";
const settle = () => new Promise(r => setTimeout(r, 0));

// routes 的 value 只放【payload】，响应外壳由上面的 fetch stub 统一包（别在这里再包一层）
const omni = approved => ({ "api/runs?session=": [{ flags: { script_approved: approved } }] });
const hypit = j => ({ "/api/hypit/studio?session=": j });

(async () => {
  // 1) omni 会话、hypit 不属于它 ⇒ 只弹流水线，绝不弹 Hypit
  let sid = { v: "session-OMNI" };
  routes = { ...omni(true), ...hypit({ up: false, reason: "not-owner" }) };
  const { ctx, calls } = makeCtx(sid);
  mod.apply(ctx);
  calls.tick(); await settle(); calls.tick(); await settle();
  check("1 omni 会话只弹流水线+运维台，绝不弹 Hypit", calls.open.map(o => o[0]).sort(), [KEY, MGKEY].sort());

  // 2) hypit 起来了（假→真）⇒ 上升沿弹一次
  routes = { ...omni(true), ...hypit({ up: true, port: 5179, run: "productions/hypit-e2e/assemble.svrun" }) };
  calls.tick(); await settle();
  check("2 studio 起了就弹（上升沿）", calls.open.map(o => o[0]).includes(HKEY), true);
  const nHypit = calls.open.filter(o => o[0] === HKEY).length;
  calls.tick(); await settle();
  check("3 持续为真不重复弹", calls.open.filter(o => o[0] === HKEY).length, nHypit);

  // 4) 归属掉了（TTL 过期 / 换 run）⇒ 必须关，且重新上膛
  routes = { ...omni(true), ...hypit({ up: false, reason: "not-owner" }) };
  calls.tick(); await settle();
  check("4 真→假会关页签（不再只开不关）", calls.close, [[sid.v, HKEY]]);
  routes = { ...omni(true), ...hypit({ up: true, port: 5179, run: "r" }) };
  calls.tick(); await settle();
  check("5 关掉后重新为真会再弹（已重新上膛）", calls.open.filter(o => o[0] === HKEY).length, nHypit + 1);

  // 6) 关口没过（还没到方案批准）⇒ 流水线页签也不弹
  const b = makeCtx({ v: "session-NEW" });
  routes = { ...omni(false), ...hypit({ up: false, reason: "not-owner" }) };
  mod.apply(b.ctx);
  b.calls.tick(); await settle();
  check("6 未过关口不弹（新会话只拿到运维台）", b.calls.open.map(o => o[0]), [MGKEY]);

  // 7) 换会话 = 记忆清零重新判定，不把上一个会话的边沿带过来
  const c = makeCtx(sid);
  routes = { ...omni(true), ...hypit({ up: true, port: 5179, run: "r" }) };
  mod.apply(c.ctx); c.calls.tick(); await settle();
  sid.v = "session-OTHER";
  c.calls.subs.forEach(fn => fn()); await settle();
  check("7 切会话后按新会话重新弹一遍", c.calls.open.filter(o => o[0] === HKEY).length, 2);

  // 8) 页签体默认是门禁，不是 iframe —— 关不掉页签时也不能把别人的引擎露在这个会话里
  const regs = captured;
  const hypitBody = regs[HKEY + "|body"];
  check("8 未绑定时 body 渲染门禁、不渲染 iframe",
    hypitBody ? JSON.stringify(hypitBody({ sessionId: "" })).includes('"tag":"iframe"') : null, false);

  console.log("\n=== %d 条断言，失败 %d ===", total, fails.length);
  for (const [n, g, w] of fails) console.log("  FAIL", n, "got", g, "want", w);
  process.exit(fails.length ? 1 : 0);
})();

window.__ModuleLoader__.load({
  id: "ops-gateway",
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;

    const PV_BUILD = "pv-20261002a";   // 排查"到底加载了哪一版"用（改这个文件时顺手 +1 后缀）
    try { window.__OPS_GW_EVAL__ = true; console.log("[pipeline-view] module evaluated", PV_BUILD); } catch (e) {}

    // 右栏工作台契约 = C:\dsh\docs\ADR-workbench-presence.md
    //   show(工作台, 会话) = 归属 ∧ 存活 ∧ 关口，缺一即不弹。
    //   归属一律来自【资源带主人】（omni=run.session / hypit=studio 正在服务的 run 的登记），
    //   绝不拿"端口活着"当跨会话信号——那是 2026-10-01/10-02 三次复发的根因。
    const GW = "http://127.0.0.1:8100";
    const VIEW_URL = "http://127.0.0.1:8765/";     // video-ui 原流水线 UI
    const KEY = "weeksir-pipeline-view";
    const HKEY = "weeksir-hypit-view";
    const MGKEY = "weeksir-model-gateway";

    function getJson(url) {
      return fetch(url).then(r => (r && r.ok ? r.json() : null)).catch(() => null);
    }

    // ── 每个工作台一个 probe：吃 sessionId，吐 {show, url, note} ────────────────────
    function probeOmni(sid) {
      if (!sid) return Promise.resolve({ show: false, note: "no-session" });
      return getJson(VIEW_URL + "api/runs?session=" + encodeURIComponent(sid)).then(runs => {
        // 关口 = 本会话有 run 且已过 Gate1.5 方案批准（立项/选方向/写剧本阶段不占右栏）
        const ok = Array.isArray(runs) && runs.some(r => r.flags && r.flags.script_approved);
        return { show: ok, url: VIEW_URL + "?session=" + encodeURIComponent(sid), note: ok ? "" : "no-approved-run" };
      });
    }

    function probeHypit(sid) {
      if (!sid) return Promise.resolve({ show: false, note: "no-session" });
      // 8100 代探：studio 只绑 [::1]:5179（Vite）且 CORS 只回显白名单 origin，浏览器直连必白屏。
      // presence 口按【会话 ↔ run】过滤，端口也由它给（换端口不用改这里）。
      return getJson(GW + "/api/hypit/studio?session=" + encodeURIComponent(sid)).then(j => {
        if (!j || !j.up) return { show: false, note: (j && j.reason) || "probe-failed" };
        return { show: true, url: "http://localhost:" + (j.port || 5179) + "/", note: j.run || "" };
      });
    }

    function probeOps() {
      // 运维台是全局面，不参与会话归属判定；但仍走同一张表、同一个对账循环（不许再写 openTab 旁路）。
      return Promise.resolve({ show: true, url: GW + "/" });
    }

    const WORKBENCHES = [
      { key: KEY,   scope: "session", label: "流水线",      srcHint: "8765", probe: probeOmni },
      { key: HKEY,  scope: "session", label: "Hypit Studio", srcHint: "5179", probe: probeHypit },
      { key: MGKEY, scope: "ops",     label: "模型网关",     srcHint: "8100", probe: probeOps },
    ];
    const byKey = {};
    for (const w of WORKBENCHES) byKey[w.key] = w;

    function titleOf(w) {
      return function Title() {
        return h("span", { title: w.scope === "ops" ? w.label + "（运维·全局）" : w.label + "（按会话归属弹出）" }, w.label);
      };
    }

    // 关不掉就降级：tabId 是 dock store 生成的，插件面只给了 closeIn(sessionId, tabId)，
    // 拿不到可靠 id ⇒ 尽力关，关不掉时由页签体自己变门禁（绝不把别人的引擎露在这个会话里）。
    function tryClose(ctx, sid, w) {
      try {
        const c = ctx.sidebarRight;
        if (c && typeof c.closeIn === "function") c.closeIn(sid, w.key);
        if (c && typeof c.closeTab === "function") c.closeTab(w.key);
      } catch (e) { /* 关不掉也要继续对账 */ }
    }

    function WorkbenchBody(props) {
      const key = props && props.__wbKey;
      const w = byKey[key];
      const sid = props && props.sessionId ? String(props.sessionId) : "";
      const state = React.useState({ show: false, url: "", note: "cold" });
      const st = state[0], setSt = state[1];
      React.useEffect(() => {
        let alive = true;
        const tick = () => w.probe(sid).then(r => { if (alive) setSt({ show: !!r.show, url: r.url || "", note: r.note || "" }); });
        tick();
        const t = setInterval(tick, 6000);
        return () => { alive = false; clearInterval(t); };
      }, [sid]);
      if (!st.show) {
        return h("div", { style: { padding: "18px 16px", lineHeight: 1.8, fontSize: 13, color: "#9aa4b2" } },
          h("div", { style: { color: "#e6e9ef", fontWeight: 600, marginBottom: 6 } }, w.label + "：本会话未绑定"),
          h("div", null, "原因：", h("code", null, st.note || "unknown")),
          h("div", { style: { marginTop: 8 } },
            "右栏按「这个会话当前拥有哪个工作台资源」弹页签；跑过一次不等于永久拥有。",
            "契约见 docs/ADR-workbench-presence.md")
        );
      }
      return h("div", { style: { width: "100%", height: "100%", display: "flex", flexDirection: "column" } },
        h("iframe", { src: st.url, title: w.label, style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" } })
      );
    }

    function bodyOf(w) {
      return function Body(props) {
        const p = Object.assign({}, props, { __wbKey: w.key });
        return h(WorkbenchBody, p);
      };
    }

    function apply(ctx) {
      try { window.__OPS_GW_APPLY__ = true; } catch (e) {}
      try {
        for (const w of WORKBENCHES) {
          ctx.effect(() => ctx.sidebarRightTabs.register({
            id: w.key, kind: w.key, priority: "extension", title: () => w.label
          }), "workbench type " + w.key);
          ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab", () =>
            ctx.slots.register({ name: "sidebar.right.pane.tab", key: w.key }, bodyOf(w))
          ), "workbench body " + w.key);
          ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab.title", () =>
            ctx.slots.register({ name: "sidebar.right.pane.tab.title", key: w.key }, titleOf(w))
          ), "workbench title " + w.key);
        }

        function curSession() {
          try { return (ctx.sessions && ctx.sessions.list && ctx.sessions.list.getSnapshot().current) || ""; } catch (e) { return ""; }
        }

        // 双向对账：每个工作台记住自己上一次判定，只在边沿动手。
        //   假→真 且上了膛 → openTab；真→假 → closeIn 并重新上膛（下次再真才弹）。
        //   "只开不关"就是第三次复发的机制来源，这里从结构上堵掉。
        const mem = {};
        function reconcile(sid) {
          for (const w of WORKBENCHES) {
            const st = mem[w.key] || (mem[w.key] = { armed: true, show: false });
            let mine = sid;
            w.probe(w.scope === "ops" ? null : sid).then(r => {
              const show = !!r.show;
              if (show === st.show) return;
              const was = st.show;
              st.show = show;
              if (show && !was && st.armed) {
                st.armed = false;
                try { ctx.sidebarRight.openTab(w.key, { revealIfOpened: true }); } catch (e) {}
              } else if (!show && was) {
                st.armed = true;
                tryClose(ctx, mine, w);
              }
            }).catch(() => {});
          }
        }
        // 换会话 = 换一整套判定：把记忆清掉重新上膛，别把上一个会话的边沿带过来
        function resetAndReconcile() { for (const k of Object.keys(mem)) delete mem[k]; reconcile(curSession()); }

        if (ctx.sidebarRight && typeof ctx.sidebarRight.openTab === "function" &&
            typeof ctx.setInterval === "function" && ctx.sessions && ctx.sessions.list) {
          try { ctx.sessions.list.subscribe(() => resetAndReconcile()); } catch (e) {}
          ctx.setInterval(() => reconcile(curSession()), 4000);
          resetAndReconcile();
        }
      } catch (e) {
        // 绝不因运维面板拖垮 DSH
        try { console.warn("[pipeline-view] register failed:", e); } catch (_) {}
      }
    }

    exports.apply = apply;
    exports.inject = ["slots", "sidebarRightTabs", "sidebarRight", "timer", "sessions"];
    return module.exports;
  }
});

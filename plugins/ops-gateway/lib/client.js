window.__ModuleLoader__.load({
  id: "ops-gateway",
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;

    try { window.__OPS_GW_EVAL__ = true; console.log("[pipeline-view] module evaluated"); } catch (e) {}

    // video-ui 原流水线 UI（agent 驱动时展示）。客户包不装本插件。
    const VIEW_URL = "http://127.0.0.1:8765/";
    const KEY = "weeksir-pipeline-view";

    function ViewTitle() {
      return h("span", { title: "流水线视图（运维）" }, "流水线");
    }

    function ViewBody(props) {
      // 会话级绑定：sidebar.right.pane.tab 是 scope:"session" 槽，body 组件从 props 拿到当前会话 sessionId
      const sid = props && props.sessionId ? String(props.sessionId) : '';
      const src = sid ? (VIEW_URL + '?session=' + encodeURIComponent(sid)) : VIEW_URL;
      return h("div", { style: { width: "100%", height: "100%", display: "flex", flexDirection: "column" } },
        h("iframe", {
          src: src,
          title: "流水线视图",
          style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" }
        })
      );
    }

    // 声明需要的服务：slots（页签 body/title）+ sidebarRightTabs（页签类型注册表）+ sidebarRight（控制器，用来 openTab）
    // "timer" 必须声明，否则 ctx.setInterval 被 guard 拒（"cannot get property setInterval without inject"）
    const inject = ["slots", "sidebarRightTabs", "sidebarRight", "timer", "sessions"];
    function apply(ctx) {
      try {
        window.__OPS_GW_APPLY__ = true;
        console.log("[pipeline-view] apply ran; slots=", !!ctx.slots, "tabs=", !!ctx.sidebarRightTabs, "ctrl=", !!ctx.sidebarRight);
      } catch (e) {}

      try {
        // ① 注册页签【类型】（extension band）——这是之前缺的关键一步
        ctx.effect(() => ctx.sidebarRightTabs.register({
          id: KEY,
          kind: KEY,
          priority: "extension",
          title: () => "流水线"
        }), "pipeline-view: tab type");

        // ② 注册页签 body（iframe）
        ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab", key: KEY }, ViewBody)
        ), "pipeline-view: body");

        // ③ 注册页签 title
        ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab.title", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab.title", key: KEY }, ViewTitle)
        ), "pipeline-view: title");

        // ④ 只在"当前会话真的有 omni run"时才弹页签（新会话 / 不做 omni 的会话不弹；页签开关是 per-seat）
        function curSession() {
          try { return (ctx.sessions && ctx.sessions.list && ctx.sessions.list.getSnapshot().current) || ""; } catch (e) { return ""; }
        }
        function syncTab() {
          try {
            const sid = curSession();
            if (!sid) return;                                          // 还没会话绑定
            if (document.querySelector('iframe[src*="8765"]')) return; // 本会话已开着，别重复弹
            fetch(VIEW_URL + "api/runs?session=" + encodeURIComponent(sid))
              .then(r => r.ok ? r.json() : [])
              // 触发点 = 本会话有 run 且【已过 Gate1.5 方案批准】(flags.script_approved)——立项/选方向/写剧本阶段不弹，创意方案确认后才弹
              .then(runs => { if (Array.isArray(runs) && runs.some(r => r.flags && r.flags.script_approved)) ctx.sidebarRight.openTab(KEY, { revealIfOpened: true }); })
              .catch(() => {});                                        // video-ui 没起/跨源失败：不弹，不崩
          } catch (e) { /* 忽略，下次再试 */ }
        }
        if (ctx.sidebarRight && typeof ctx.sidebarRight.openTab === "function" &&
            typeof ctx.setInterval === "function" && ctx.sessions && ctx.sessions.list) {
          try { ctx.sessions.list.subscribe(() => syncTab()); } catch (e) {}
          ctx.setInterval(syncTab, 4000);
          syncTab();
        }
      } catch (e) {
        // 绝不因运维面板拖垮 DSH
        try { console.warn("[pipeline-view] register failed:", e); } catch (_) {}
      }
    }

    exports.apply = apply;
    exports.inject = inject;
    return module.exports;
  }
});

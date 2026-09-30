window.__ModuleLoader__.load({
  id: "ops-gateway",
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;

    try { window.__OPS_GW_EVAL__ = true; console.log("[pipeline-view] module evaluated"); } catch (e) {}

    // video-ui 流水线只读单栏视图（agent 驱动时展示；自动聚焦最新 run）。客户包不装本插件。
    const VIEW_URL = "http://127.0.0.1:8765/focus";
    const KEY = "weeksir-pipeline-view";

    function ViewTitle() {
      return h("span", { title: "流水线视图（运维）" }, "流水线");
    }

    function ViewBody() {
      return h("div", { style: { width: "100%", height: "100%", display: "flex", flexDirection: "column" } },
        h("iframe", {
          src: VIEW_URL,
          title: "流水线视图",
          style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" }
        })
      );
    }

    // 声明需要的服务：slots（页签 body/title）+ sidebarRightTabs（页签类型注册表）+ sidebarRight（控制器，用来 openTab）
    // "timer" 必须声明，否则 ctx.setInterval 被 guard 拒（"cannot get property setInterval without inject"）
    const inject = ["slots", "sidebarRightTabs", "sidebarRight", "timer"];
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

        // ④ 自动打开：openTab 需要已绑定的会话，故轮询重试，一旦成功就停（setInterval 随 fiber 生命周期回收）
        if (ctx.sidebarRight && typeof ctx.sidebarRight.openTab === "function" && typeof ctx.setInterval === "function") {
          let opened = false;
          ctx.setInterval(() => {
            if (opened) return;
            try {
              ctx.sidebarRight.openTab(KEY, { revealIfOpened: true });
              opened = true;
              console.log("[pipeline-view] openTab ok");
            } catch (e) { /* 还没会话绑定，继续等 */ }
          }, 1500);
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

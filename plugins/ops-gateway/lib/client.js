window.__ModuleLoader__.load({
  id: "ops-gateway",
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;

    // 诊断信标：确认插件模块被求值 / apply 被调用
    try { window.__OPS_GW_EVAL__ = true; console.log("[ops-gateway] module evaluated"); } catch (e) {}


    // 运维端模型配置面板（model_admin）。仅本机；客户包不装本插件。
    const GATEWAY_URL = "http://127.0.0.1:8100/";
    const KEY = "weeksir-api-gateway";

    function GatewayTitle() {
      return h("span", { title: "API 网关（运维）" }, "API网关");
    }

    function GatewayBody() {
      return h("div", { style: { width: "100%", height: "100%", display: "flex", flexDirection: "column" } },
        h("iframe", {
          src: GATEWAY_URL,
          title: "API 网关",
          style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" }
        })
      );
    }

    const inject = ["slots"];
    function apply(ctx) {
      try { window.__OPS_GW_APPLY__ = true; console.log("[ops-gateway] apply ran; ctx.slots=", !!ctx.slots); } catch (e) {}
      try {
        ctx.slots.inject("sidebar.right.pane.tab", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab", key: KEY }, GatewayBody)
        );
        ctx.slots.inject("sidebar.right.pane.tab.title", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab.title", key: KEY }, GatewayTitle)
        );
      } catch (e) {
        // 绝不因运维面板拖垮 DSH
        try { console.warn("[ops-gateway] inject failed:", e); } catch (_) {}
      }
    }

    exports.apply = apply;
    exports.inject = inject;
    return module.exports;
  }
});

window.__ModuleLoader__.load({
  id: "ops-gateway",
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    Object.defineProperty(exports, Symbol.toStringTag, { value: "Module" });
    const React = require("react");
    const h = React.createElement;

    const PV_BUILD = "pv-20261001c";   // 排查"到底加载了哪一版"用（改这个文件时顺手 +1 后缀）
    try { window.__OPS_GW_EVAL__ = true; console.log("[pipeline-view] module evaluated", PV_BUILD); } catch (e) {}

    // video-ui 原流水线 UI（agent 驱动时展示）。客户包不装本插件。
    const VIEW_URL = "http://127.0.0.1:8765/";
    const KEY = "weeksir-pipeline-view";
    // hypit 官方工作台 = `hypit studio`（Vite dev server）。实测两件事决定了这里的写法：
    //   ① 它**只监听 IPv6 回环** [::1]:5179（netstat 实证），写 127.0.0.1 连不上 ⇒ 必须用 localhost；
    //   ② 它有只读口 GET /__studio/library?section=tasks（一卡一条 Build 的状态/失败原因），
    //      但 Vite 的 CORS 只回显白名单 origin（实测 dsh-app://、null 都不给 ACAO）⇒ 本插件 fetch 不到，
    //      所以探活走 8100 的代探只读口 /api/hypit/studio（那边带 ACAO:*）。
    //   不再挂 D 控制台（:8090/:8000 已弃用于 hypit）。
    const HYPIT_STUDIO_URL = "http://localhost:5179/";
    const HYPIT_PROBE = "http://127.0.0.1:8100/api/hypit/studio";
    const HKEY = "weeksir-hypit-view";
    // 模型网关管理台 = model_admin(:8100)：列能力/改价/看健康/写 .env 密钥。运维专用、非会话绑定、客户包不含。
    const MGMT_URL = "http://127.0.0.1:8100/";
    const MGKEY = "weeksir-model-gateway";

    function ViewTitle() {
      return h("span", { title: "流水线视图（运维）" }, "流水线");
    }

    function HypitTitle() {
      return h("span", { title: "Hypit 官方工作台（agent 起 hypit studio --run X --port 5179；未 build 也能开，看 Source/Tasks/Artifacts）" }, "Hypit Studio");
    }

    function IFrameBody(url, label) {
      return h("div", { style: { width: "100%", height: "100%", display: "flex", flexDirection: "column" } },
        h("iframe", {
          src: url,
          title: label,
          style: { flex: 1, width: "100%", height: "100%", border: 0, background: "#0f1115" }
        })
      );
    }

    function ViewBody(props) {
      // 会话级绑定：sidebar.right.pane.tab 是 scope:"session" 槽，body 组件从 props 拿到当前会话 sessionId
      const sid = props && props.sessionId ? String(props.sessionId) : '';
      const src = sid ? (VIEW_URL + '?session=' + encodeURIComponent(sid)) : VIEW_URL;
      return IFrameBody(src, "流水线视图");
    }

    function HypitBody() {
      // hypit 官方工作台：agent 跑 `hypit studio --run <x>.svrun --port 5179` 后 iframe 即有内容。
      // 一个 studio 只开一个 run（--run 绑死），多工程时要换端口/换进程重启。
      return IFrameBody(HYPIT_STUDIO_URL, "Hypit Studio");
    }

    function MgmtTitle() {
      return h("span", { title: "模型网关管理台 model_admin:8100（运维）" }, "模型网关");
    }

    function MgmtBody() {
      // 全局运维台，非会话绑定：列 ROUTES/改价/看健康/写 .env。8100 没起则白屏（手动起 model_admin）。
      return IFrameBody(MGMT_URL, "模型网关管理台");
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

        // ③'. Hypit Studio tab：类型 + body(iframe→native studio :5179) + title
        ctx.effect(() => ctx.sidebarRightTabs.register({
          id: HKEY,
          kind: HKEY,
          priority: "extension",
          title: () => "Hypit Studio"
        }), "pipeline-view: hypit tab type");
        ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab", key: HKEY }, HypitBody)
        ), "pipeline-view: hypit body");
        ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab.title", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab.title", key: HKEY }, HypitTitle)
        ), "pipeline-view: hypit title");

        // ③''. 模型网关管理台 tab（运维·全局·非会话门控）：类型 + body(iframe→:8100) + title
        ctx.effect(() => ctx.sidebarRightTabs.register({
          id: MGKEY,
          kind: MGKEY,
          priority: "extension",
          title: () => "模型网关"
        }), "pipeline-view: model-gateway tab type");
        ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab", key: MGKEY }, MgmtBody)
        ), "pipeline-view: model-gateway body");
        ctx.effect(() => ctx.slots.inject("sidebar.right.pane.tab.title", () =>
          ctx.slots.register({ name: "sidebar.right.pane.tab.title", key: MGKEY }, MgmtTitle)
        ), "pipeline-view: model-gateway title");
        // 运维台常驻：启动即开一次（关了可从右栏页签带再点开）
        try { if (ctx.sidebarRight && typeof ctx.sidebarRight.openTab === "function") ctx.sidebarRight.openTab(MGKEY, { revealIfOpened: true }); } catch (e) {}

        // ④ 只在"当前会话真的有 omni run"时才弹页签（新会话 / 不做 omni 的会话不弹；页签开关是 per-seat）
        function curSession() {
          try { return (ctx.sessions && ctx.sessions.list && ctx.sessions.list.getSnapshot().current) || ""; } catch (e) { return ""; }
        }
        function syncOmniTab(sid) {
          try {
            if (!sid) return;                                          // 还没会话绑定
            if (document.querySelector('iframe[src*="8765"]')) return; // 本会话已开着，别重复弹
            fetch(VIEW_URL + "api/runs?session=" + encodeURIComponent(sid))
              .then(r => r.ok ? r.json() : [])
              // 触发点 = 本会话有 run 且【已过 Gate1.5 方案批准】(flags.script_approved)——立项/选方向/写剧本阶段不弹，创意方案确认后才弹
              .then(runs => { if (Array.isArray(runs) && runs.some(r => r.flags && r.flags.script_approved)) ctx.sidebarRight.openTab(KEY, { revealIfOpened: true }); })
              .catch(() => {});                                        // video-ui 没起/跨源失败：不弹，不崩
          } catch (e) { /* 忽略，下次再试 */ }
        }
        let hypitArmed = true;   // 上升沿才弹：studio「没起→起了」才自动开页签；人主动关掉后不再骚扰，直到它重新起一次
        function syncHypitTab(sid) {
          // 触发信号 = **本会话**自己 claim 过的 studio 活着（8100 代探 + 按会话主人过滤）。
          // studio 只有一个端口、也没有 DSH 会话概念 ⇒ 主人由 agent 起 studio 前登记
          //   python D:\video-skill\backend\hypit_bridge.py studio-claim "$DSH_SESSION_ID" <run>
          // 不传/不匹配 session 一律不弹（否则 omni 会话会被弹出 Hypit 工作台，2026-10-01 踩过）。
          try {
            if (!sid) return;
            fetch(HYPIT_PROBE + "?session=" + encodeURIComponent(sid))
              .then(r => r.ok ? r.json() : null)
              .then(j => {
                if (!j || !j.up) { hypitArmed = true; return; }          // 没在跑：重新上膛
                if (document.querySelector('iframe[src*="5179"]')) return;  // 已经开着
                if (!hypitArmed) return;                                // 弹过后被人为关掉：本次不再重开
                hypitArmed = false;
                ctx.sidebarRight.openTab(HKEY, { revealIfOpened: true });
              })
              .catch(() => {});
          } catch (e) { /* 忽略，下次再试 */ }
        }
        function syncTab() {
          const sid = curSession();
          syncOmniTab(sid);
          syncHypitTab(sid);
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

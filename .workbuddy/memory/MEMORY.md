# 项目长期记忆（DSH / WEEKSIR Desktop）

## 重建链（_setup/rebuild_weeksir_desktop.py）
- **必须用系统 Python 3.12 跑**（`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`）——apply_weeksir_desktop_brand 依赖 Pillow，managed 3.13 无 PIL 会在第 2 步断链。
- 全链幂等已验证（2026-09-27）：patch_reap_harness / patch_harness_stability 复跑零改动；干跑基线产物与安装态 8/8 标记一致。
- exe 图标步骤（rcedit）需 DSH Desktop 完全退出；运行中会报 `fail:1: Unable to commit changes` 但不中断链（图标早已设置，无损失）。
- preset 禁用（minimal/ptc/cordis）与 harness 健康检查加固**需重启应用生效**。

## 铁律
- 改 DSH 渲染层 UI 一律纯 DOM 注入（MutationObserver），禁 React hook、禁 ctx.slots.register 注册自定义组件到核心槽（品牌标除外）。
- `C:\dsh\plugins\super-clone-ui\lib\client.js` 是用户/并行会话实时开发的文件（hero tabs、模式切换、账号切换器、hidePptButtons 均在此）——**任何脚本不得整体覆盖**；slim_ui_plugin.py 已加 UI 标记跳过保护；brand 补丁只做 logo URI 无损回写。
- 修改后一键重建桌面端：`python rebuild_weeksir_desktop.py`（默认就地重补；`--install` 为全新装机）。


## 2026-10-09 第 5 处补丁：任务/活跃时长改由「回合」记账（方案 1）

- 判据（用户拍定）：**一个回合动过手（本轮有 tool/call）且正常收尾（turn/end 且 reason 非 blocked）= 一次任务**；活跃时长 = 同回合 turn/start→turn/end 墙钟（仍走作者 recordActive，单次封顶 5 分钟）。
- 为什么：改前账本 tasksDone=1 / activeMs=0 / sessions=10 —— 两条账都挂在 ctx.jobs（后台任务服务）上，前台回合不产生 job，所以长期个位数、时长恒 0。
- 落点：whale-girl/lib/index.mjs，rev weeksir-turn-account-20261009a；补丁脚本 scripts/patch-whale-girl-turn-account.py；备份 index.mjs.bak-weeksir-turn-account-20261009；闸 scripts/check-pet-turn-account.py 23 条（T 组 node 真跑 turnAccount 值表 + S 组形状）。
- 🔴 后台 job 完成**不再计第二次**（发起它的回合已计），那条分支只保留庆祝窗口；job 失败仍记 failures。
- 生效条件：这是 node 侧 bundle ⇒ **必须完全重启 DSH**（Ctrl+R 不够）。

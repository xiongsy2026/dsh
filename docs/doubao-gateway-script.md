# 豆包网页版生图（doubao）网关腿 · 脚本手册

> 唯一源：`backend/gateway.py` 的 `_doubao_image()`（`:843`）+ `call()` 分支（`:2975`）
> + 探活分支（`:544`）+ `config.py:322-326` 三个键；执行脚本 = `VIDEO_UI_ROOT/doubao.py`（video-ui 那份，不是网关的）。
> 本文只做整理与可抄脚本，判据以那边为准；网关总口径见 `docs/ADR-model-gateway.md`。
> 整理时间：2026-10-14（`doubao.py` 盘上 mtime 2026-10-04 09:09）。

## 一分钟认识这条腿

- **是什么**：豆包网页版生图（Seedream）包进网关的一条 lane = **`image.generate/doubao`**
  （`provider=doubao-web`，走 subprocess 调 video-ui 的 `doubao.py gen`，**Playwright 驱动本机 Chrome**，不是 HTTP API）。
- **没有 API key，只有一个登录态**（`doubao_state.json` 或 `doubao_profile/`）⇒ 网关 `key_present: False` 是正常的。
- **计费**：我方账本记 **0 分**（`gateway.py:254`，"不谎报扣费"）——**免批**（`image.generate` 在 `SPEND_APPROVAL_EXEMPT`，
  `gateway.py:3182`）⇒ 直接干不等点头；仍走 预扣(0)/结算/审计，跑完报实扣 0 分。
- **必须给落盘路径**：它只落盘、不回 URL，没 `output_path` 直接拒。
- **成功判据不是"退出码 0"**：`exit 0 + 文件存在 + 文件 > 1024 字节` 三条都算（`gateway.py:883`）——
  小于 1KB 多半是张错误页截图/空图。
- **串行**：与 gflow 腿抢同一个浏览器 ⇒ 网关 `_BROWSER_LOCK` 串行化（`gateway.py:805-808`）。

## 调用脚本（三选一）

### ① 标准：经网关（有账本留痕，生产一律走这条）

```bash
python backend/gateway.py call image.generate doubao --account local --json '{"prompt":"一只穿毛衣的柯基站在雪地里，产品特写","output_path":"C:/dsh/productions/<task>/outputs/doubao-01.png"}'
```

参数键（`call()` 分支 `gateway.py:2975-2983`）：

| 键 | 别名 | 必给 | 说明 |
|---|---|---|---|
| `prompt` | `text_prompt` | ✅ | 一句提示词 |
| `output_path` | — | ✅ | 落盘绝对路径（只落盘不回 URL）；成功还要求文件 >1024 字节 |
| `image_urls` | `images` / `refs` | — | 参考图，**本机文件路径**（公网 URL 当场拒）；可多张，逐张 `--ref` |
| `model` | — | — | 默认读 `DOUBAO_IMAGE_MODEL`（现 `Seedream 4.5`） |
| `timeout` | — | — | 默认 300 秒（整条含浏览器启动） |
| `headless` | — | — | 默认 **false（有头）**，见坑 2；`true` 走 Chrome 新版无头（Playwright 1.61 + channel=chrome，真渲染） |
| `win_pos` | — | — | 有头时窗口位置 `x,y`（Chrome `--window-position`） |
| `win_size` | — | — | 窗口大小 `w,h`（Chrome `--window-size`）；给了 pos+size 时视口自动缩到 `max(640,w-40) × max(480,h-160)` |

⚠️ `win_pos/win_size` **只能挪到屏内**：挪出屏幕 = 窗口未渲染 = 掉登录（`gateway.py:850`）。

返回信封同全网关：`{ok, result, credits:{estimated,charged,basis,balance_after}, error}`；
`result` 里有 `path / bytes / model / ms / exit_code / headless / window`。

### ② 只读探测（0 分、不发起生成）

```bash
python backend/gateway.py resolve image.generate doubao     # 这条腿是谁：provider/model/key_present
python backend/gateway.py prices                            # 价目（找 image.generate/doubao 那一行：0 分 [doubao-web]）
```

探活（`gateway.py:544-560`）只查三件：`DOUBAO_PYTHON` 与 `doubao.py` 在不在 → 那个解释器 `import playwright` 通不通
→ `doubao_state.json` 在不在（**只是登录态文件在，真通不通要实跑一条**），回 `reachable` / `auth-fail` / `config-error`。

### ③ 直接用 doubao.py（排障用；绕过账本，别当生产入口）

```bash
cd C:\dsh\scratch\video-ui\video-ui

python doubao.py login                      # 弹 Chrome 手动登一次，关掉窗口即保存登录态
python doubao.py probe                      # 登录后探测页面结构，截图+候选选择器写 _doubao_probe/
python doubao.py diagnose "提示词" --wait 90 # 出问题时跑现场诊断（每步截图）
python doubao.py models --chips 模型         # 读页面上的模型档位（现默认 Seedream 4.5）
python doubao.py wait-login                 # 等登录完成（不关窗口时用）
python doubao.py use-chrome                              # 切回自己的 Chrome profile
python doubao.py use-360 --url <可选>                    # 切到 360 浏览器那套登录态（见下"浏览器来源"）
python doubao.py gen "提示词" --ref a.jpg --ref b.jpg --out C:/tmp/doubao.png --model "Seedream 4.5" --headed
```

`gen` 的参数（`doubao.py:767-772`）：`prompt`（必给）、`--ref`（可重复）、`--out`（**必给**）、
`--model`（默认 `Seedream 4.5`）、`--headed`（有头；不给 = 无头，⚠️ 见坑 2）。

## 配置键（`config.py`，`.env` 可覆盖）

| 键 | 默认 | 作用 |
|---|---|---|
| `DOUBAO_PYTHON` | `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe` | ⚠️ 必须是**装了 playwright 的那个解释器**（= 跑 video-ui :8765 那个），**不是网关所在的 .venv**（venv 没 playwright，子进程一上来 ModuleNotFound） |
| `VIDEO_UI_ROOT` | `C:\dsh\scratch\video-ui\video-ui` | `doubao.py` 与 profile 目录所在 |
| `DOUBAO_IMAGE_MODEL` | `Seedream 4.5` | `--model` 默认档（档位以 `models --chips 模型` 读页面为准） |
| `DOUBAO_WIN_POS` / `DOUBAO_WIN_SIZE` | 空 | 由网关按 `win_pos`/`win_size` 注入；格式 `x,y` / `w,h` |

**浏览器来源**（`doubao.py` 顶部）：`doubao_browser.json` 记 `{"browser": "chrome"|"360"}`——
`chrome` = 自己的 profile `doubao_profile/`（无 `doubao_state.json` 时用持久化上下文）；
`360` = 从 360 浏览器拷的登录态 `doubao_360_profile/`（`use-360` 切过去，360 程序在 `%APPDATA%\360se6`）。
有 `doubao_state.json` 时则走 `storage_state` 注入（浏览器本体是普通 launch）。
默认视口 1440×900、locale zh-CN。

## 坑（都是盘上踩过的，别再踩）

1. 🔴 **豆包弹图形验证码是这条腿最常见的死法**：2026-10-04 实测无头跑到"发送提示词"成功，随后被
   「请选择所有符合上文描述的图片并拖拽」的验证窗口挡住，一直等到超时 ⇒ **这条默认有头**（`--headed`）。
   超时不一定=慢：`_doubao_image` 会看 `_doubao_probe/gen_05_sent.png` 的 mtime，如果是本次新写的
   ⇒ 提示词已发出、卡在等回图，多半就是验证码（`gateway.py:884-890`）。
2. **headless 不是"传了就算"**：2026-10-04 之前 `doubao.py` 的 `launch()` 在有登录态时硬编码有头、
   调用方传什么都不算；那行已放开，所以网关现在照传。⚠️ 但**豆包会识别 headless 并掉登录**，
   新版无头（Playwright 1.61 + channel=chrome）能不能稳过没验证过 ⇒ 想无头自己跑一条验，别默认开。
3. **退出码 0 不等于出图**：判据是 `exit 0 + 文件存在 + >1024 字节`；两种已知"没落到文件"形态：
   ① 登录态掉了（`python doubao.py login` 重登）② 图形验证码（见坑 1）。
4. **参考图只收本机文件**，公网 URL 当场拒（`gateway.py:865-867`）；它自己负责上传。
5. **窗口必须在屏内**：`win_pos` 挪出屏幕 = 未渲染 = 掉登录（`gateway.py:850`）。给 pos+size 时视口会自动缩，
   否则页面按 1440 排版会溢出（`doubao.py:108-113`）。
6. **`DOUBAO_PYTHON` 指错解释器**是配置类第一死法：报 `ModuleNotFoundError: playwright` 就是它
   （网关探活会先 `import playwright` 验一遍，回 `config-error`）。
7. **登录态只认文件在不在**：探活回 `reachable` 只说明 `doubao_state.json` 存在，cookie 过期要实跑一条才知道；
   `login` 的规矩是"弹出 Chrome 手动登一次，**关掉窗口即保存**"。
8. **两条网页腿抢同一个浏览器**：doubao 与 gflow 共用 `_BROWSER_LOCK`，绕开网关手跑两条就是自己撞自己。
9. **报错文案别放 `⇒`**：2026-10-06 实测拒绝消息里带这个字符，8100 的 cp936 stdout 会 UnicodeEncodeError
   （`gateway.py:4626` 的教训）——文案用中文标点。

## 出问题怎么查（按顺序，0 分）

```bash
cd C:\dsh\scratch\video-ui\video-ui
python doubao.py probe                                   # ① 登录后页面结构，截图在 _doubao_probe/
python backend/gateway.py resolve image.generate doubao   # ② 网关这边认的是哪条腿/哪个模型
python backend/gateway.py call image.generate doubao --account local --json '{"prompt":"test","output_path":"C:/tmp/doubao/probe.png"}'   # ③ 小图试一条
```

判据：`_doubao_probe/gen_05_sent.png` 是本次新写的而没出图 ⇒ 卡在验证码/回图；
文件在但 <1024 字节 ⇒ 当失败看（多半是错误页截图）；报 playwright 缺失 ⇒ 换 `DOUBAO_PYTHON`；
全程 0 分、不动批准额度（免批），但**会上账本**。

# Google Flow（gflow）网关腿 · 脚本手册

> 唯一源：`backend/gateway.py` 的 `_gflow_image()`（`:904`）+ `call()` 分支（`:2984`）
> + `config.py:327-337` 的五个键。本文只做整理与可抄脚本，判据以那边为准；
> 改了那边要回来同步这张表（其它口径见 `docs/ADR-model-gateway.md`）。
> 整理时间：2026-10-14；盘上版本 = `gflow-cli 0.80.0`（MIT，ffroliva/gflow-cli）。

## 一分钟认识这条腿

- **是什么**：Google Flow 的命令行（Imagen / Nano Banana 生图）包进网关的一条 lane = **`image.generate/gflow`**
  （`provider=gflow-cli`，走 subprocess，不是 HTTP API）。
- **没有 API key，只有一个 Google 登录态**（`gflow auth` 的 profile）⇒ 网关里 `key_present: False` 是正常的。
- **计费**：我方账本记 **0 分**（`gateway.py:252-254`，注释写明"不谎报扣费"）——真烧的是 **Google Flow 的额度**，
  不是 weeksir 积分。**免批**（`image.generate` 在 `SPEND_APPROVAL_EXEMPT` 里，`gateway.py:3182`）⇒ 直接干、不等点头；
  但仍走同一套 预扣(0)/结算/审计，跑完照旧报实扣 0 分。
- **必须给落盘路径**：Flow 侧的链接会过期，没 `output_path` 直接拒（`_gflow_image` 自己拦）。
- **串行**：doubao 网页腿与 gflow 都要抢浏览器 ⇒ 网关用 `_BROWSER_LOCK` 串行化（`gateway.py:805-808`），并发调用会排队。

## 调用脚本（三选一）

### ① 标准：经网关（有账本留痕，生产一律走这条）

```bash
python backend/gateway.py call image.generate gflow --account local --json '{"prompt":"一只穿毛衣的柯基站在雪地里，产品特写","output_path":"C:/dsh/productions/<task>/outputs/gflow-01.png"}'
```

参数键（`call()` 分支 `gateway.py:2984-2993`，大小写/别名已归一）：

| 键 | 别名 | 必给 | 说明 |
|---|---|---|---|
| `prompt` | `text_prompt` | ✅ | 一句提示词（多条提示词不走 project，见坑 7） |
| `output_path` | — | ✅ | 落盘绝对路径；gflow 自己命名，网关再挪到你要的这个名字 |
| `image_urls` | `images` / `refs` | — | 参考图，**本机文件路径**（不是 URL，不是公网直链）；给了就走 `i2i` |
| `model` | — | — | 默认读 `GFLOW_IMAGE_MODEL`（现 `nano2`），可临时指定见下表 |
| `aspect` | `ratio` | — | 默认 `9:16`；合法值见下表 |
| `project` | — | — | Flow 项目 id；不传用 `GFLOW_PROJECT`。⚠️ 只在**无参考图**时才会加上 |
| `headless` | — | — | 默认 false。⚠️ 见坑 5：有头≠不会被拒 |
| `timeout` | — | — | 默认 300 秒（含登录探测） |

返回是归一信封 `{ok, result, credits:{estimated,charged,basis,balance_after}, error}`；
`result` 里有 `path / source / bytes / model / mode / ms / exit_code`。

### ② 只读探测（0 分、不发起生成）

```bash
python backend/gateway.py resolve image.generate gflow    # 这条腿是谁：provider/model/key_present
python backend/gateway.py prices                          # 价目（找 image.generate/gflow 那一行：0 分 [gflow-cli]）
```

### ③ 直接用 CLI（排障用；绕过账本，别当生产入口）

```bash
"C:\Users\Administrator\.local\bin\gflow-cli.exe" auth status            # 只读查登录态（慢网可达 ~45s）
"C:\Users\Administrator\.local\bin\gflow-cli.exe" auth                  # 看 profile 清单（没 profile 会自动引导 login）
"C:\Users\Administrator\.local\bin\gflow-cli.exe" auth login            # 新建/换登录 profile

"C:\Users\Administrator\.local\bin\gflow-cli.exe" image t2i "提示词" --model nano2 --aspect 9:16 --out C:/tmp/gflow --json
"C:\Users\Administrator\.local\bin\gflow-cli.exe" image i2i "提示词" --ref C:/tmp/hero.png --model nano-pro --aspect 3:4 --out C:/tmp/gflow --json
"C:\Users\Administrator\.local\bin\gflow-cli.exe" image upload C:/tmp/hero.png   # 只上传拿 asset UUID
```

## CLI 真实参数（0.80.0 源码 `gflow_cli/cli_image.py`、`image_batch.py`）

`gflow image` 三个子命令：`upload` / `t2i` / `i2i`。

| 参数 | 适用 | 说明 |
|---|---|---|
| `--model` | t2i/i2i | 档位固定五选一：`nano2`（快）· `nano2-lite`（轻）· `nano-pro`（均衡）· `image4` · `imagen4`（`image_batch.ALLOWED_MODELS`） |
| `--aspect` | t2i/i2i | 五选一：`9:16` `16:9` `1:1` `4:3` `3:4` |
| `-n/--count` | t2i/i2i | 出几张（1–4） |
| `--out DIR` | t2i/i2i | 目录；不给则落 `<output_dir>/images/<YYYY-MM-DD>/`（按日期分），给了就平铺 `<dir>/<media>_<n>.png` |
| `-o/--output FILE` | t2i/i2i | 指定单个文件名；**单条提示词限定** |
| `--ref PATH`（可重复） | i2i | 参考图本地路径（软链会被 resolve，防"软链指向私钥"外传） |
| `--project` / `--project-name` | t2i/i2i | Flow 项目；**多条提示词时不生效**（gflow 自己的限制） |
| `--reference-entity` / `--reference-entity-name` | t2i/i2i | 已存的角色/资产引用（`@Name` 内联同源） |
| `--json` | 全部 | 出机器可读结果（网关先按它找落盘路径） |
| `--stdin` / `--prompts-file` | t2i | 多提示词；`--continue-on-error/--fail-fast` |
| `--profile` / `--transport` | 全部 | 覆盖默认 profile / 传输层 |
| `--instruction/-i`、`--ui-mode` | t2i/i2i | agentic 模式用；网关不传 |

顶层还有（网关没接，要接必须走 gateway 加路由，别绕开）：`auth` `movie` `video`（Veo 文生/图生视频）
`character` `scene` `project` `credits` `models` `run` `tools` `data` `docs` `update` `instructions`
`mcp`（MCP server）`serve`（`--port 8000 --host 127.0.0.1` 守护进程）。

## 配置键（`config.py`，`.env` 可覆盖）

| 键 | 默认 | 作用 |
|---|---|---|
| `GFLOW_EXE` | `C:\Users\Administrator\.local\bin\gflow-cli.exe` | 可执行文件；不存在时退到 PATH 里的 `gflow`，都没有 ⇒ 明确报错 |
| `GFLOW_IMAGE_MODEL` | `nano2` | `--model` 的默认档 |
| `GFLOW_PROJECT` | `75cd0b95-7ffb-4df1-8d5b-688ff9e20d10` | `--project` 默认值（本账号项目） |
| `GFLOW_TRANSPORT` | `sapisidhash` | 传输层，见下表 |
| `GFLOW_CLI_TRANSPORT` / `GFLOW_CLI_EXPERIMENTAL_TRANSPORTS` / `GFLOW_CLI_HEADLESS` | 由网关注入 | 直接用 CLI 时才需要自己设：非 `ui_automation` 的传输要 `GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1` 才在 CLI 里可选 |

**传输两条腿**（`config.py:327-333`）：

| 传输 | 弹浏览器窗 | 代价 |
|---|---|---|
| `ui_automation`（CLI 默认） | **必须有头** | gflow 自己写明 reCAPTCHA Enterprise 对无头直接 403 |
| `sapisidhash`（网关现默认） | 不弹（headless） | 上游标 experimental：Google WAF 会拒"auth 对但指纹不对"的请求，且可能回**空媒体的假 200** |

## 坑（都是盘上踩过的，别再踩）

1. 🔴 **退 0 但没落盘文件 = 失败**，必须报错（`_gflow_image` 尾部那条判据不许拆）——`sapisidhash` 会回假 200。
2. **`--json` 曾长期形同虚设**：代码里写的是 `_j.loads`，而 `_j` 是别处函数内的别名、这里没绑 ⇒ `NameError`
   被 `except` 吃掉，每次都走"扫目录取最新 png"的兜底（同目录多会话并发会拿错文件）。现在按
   `path/file/output_path/local_path/dest/saved` 找，找不到才兜底（`gateway.py:971-982`）。
3. **`auth status` 慢网可达 ~45s**：30s 超时会把"探测超时"误报成"没登录"（2026-10-10 实测，手工跑退 0、适配器拿到 code=None）。
   网关现在给 75s，且**探测超时 ≠ 没登录 ⇒ 不发起生成**，先确认到 Google 的网络通不通再重试同一条。
4. 🔴 **WafRejectionError（exit 10）与 `PUBLIC_ERROR_UNUSUAL_ACTIVITY`**：2026-10-04 无头/有头各跑一次都进得了 Flow
   项目页、UI 逐字段相同，却都被打回——被打分的是 **gflow 那个 Playwright profile**，不是账号
   （`auth status` 当场有效，同一账号浏览器里能出图）。换 project、换提示词都没用 ⇒ 只有"等几小时"或
   `gflow auth login` 换 profile。**headless 不是变量**，别在它上面找原因。
5. **`--ref` 只收本机文件路径**（网关会先校验存在，不存在当场点名）；它是 URL 就报错。
6. **`--project` 单条提示词才有效**，且网关在给了参考图时不传 project（`gateway.py:963-965`）。
7. **版本漂移会让请求一个都不发出去**（2026-10-06），而批准额度已经掉段 ⇒ 所以失败必须退段（网关已做：
   `gateway.py:4254`、`:4576`）。看到"额度掉了但没出图"= 失败，不是成功。
8. **输出目录会被 gflow 自己命名**：要固定文件名就给 `output_path`/`-o`，别指望 `--out` 里出现你起的名字。
9. **并发别直跑 CLI**：与 doubao 腿抢同一个浏览器，网关那层有锁，绕开网关手跑两条就是自己撞自己。

## 出问题怎么查（按顺序，0 分）

```bash
"C:\Users\Administrator\.local\bin\gflow-cli.exe" auth status            # ① 登录态还在不在
python backend/gateway.py resolve image.generate gflow    # ② 网关这边认的是哪条腿/哪个模型
python backend/gateway.py call image.generate gflow --account local --json '{"prompt":"test","output_path":"C:/tmp/gflow/probe.png"}'   # ③ 小图试一条
```

判据：① 退 0 但没文件 ⇒ 按失败（假 200）；② exit 10 / unusual activity ⇒ profile 被打分，换 profile 或等；
③ `GFLOW_EXE 不存在` ⇒ 装 `gflow-cli` 或把路径配进 `.env`；④ 全程 0 分、不动批准额度（免批），但**会上账本**。

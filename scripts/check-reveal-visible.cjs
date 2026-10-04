// 交付卡「在文件资源管理器中显示」回归闸
//   静态：内核 revealNativePath 不许再用 file:// URL、不许给 explorer 传 windowsHide
//   实跑：调【装好的那个真函数】，断言目录窗口 IsWindowVisible=true（旧版是 vis=False ⇒ 用户看不见）
// 用法：node scripts/check-reveal-visible.cjs [--no-live]
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const APP = "C:\\Users\\Administrator\\AppData\\Local\\Programs\\DSH Desktop\\resources\\app\\node_modules\\@deepseek-ai";
const TARGET = path.join(APP, "dsh-native-command", "lib", "index.js");
const LIVE = !process.argv.includes("--no-live");
const ROOT = "C:\\dsh\\.tmp-reveal-check";

let pass = 0, fail = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  console.log((ok ? "PASS " : "FAIL ") + name + (ok ? "" : ` => ${JSON.stringify(got)} (期望 ${JSON.stringify(want)})`));
  if (ok) pass++; else fail++;
};

const PS = (b) => execFileSync("powershell.exe", ["-NoProfile", "-Command", b], { encoding: "utf8" }).trim();
const VIS = "Add-Type -Namespace W -Name V -MemberDefinition '[DllImport(\"user32.dll\")] public static extern bool IsWindowVisible(IntPtr h);';" +
  "$s=New-Object -ComObject Shell.Application;$n=0;$v=0;" +
  "$s.Windows()|ForEach-Object{ if($_.LocationURL -like $args[0]){ $n++; if([W.V]::IsWindowVisible([IntPtr]$_.HWND)){$v++} } };" +
  "'{0}/{1}' -f $v,$n";
const countVisible = (urlLike) => {
  const out = PS("$a='" + urlLike + "';" + VIS.replace("$args[0]", "$a"));
  const m = /^(\d+)\/(\d+)$/.exec(out);
  return m ? { visible: Number(m[1]), total: Number(m[2]) } : { visible: -1, total: -1, raw: out };
};
const closeUrl = (urlLike) => PS("$s=New-Object -ComObject Shell.Application;$s.Windows()|ForEach-Object{ if($_.LocationURL -like '" + urlLike + "'){ try{$_.Quit()}catch{} } }");
const wait = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  const src = fs.readFileSync(TARGET, "utf8");
  const explorerBlock = /if \(manager === "explorer"\) \{[\s\S]*?\n\t\}/.exec(src);
  check("1 找得到 explorer 分支", !!explorerBlock, true);
  const blk = explorerBlock ? explorerBlock[0] : "";
  check("2 不再把路径转成 file:// URL（中文路径开不出窗口）", blk.includes("pathToFileURL"), false);
  check("3 /select, 与路径拼成【一个】参数", /\/select,"?\$\{?windowsPath/.test(blk), true);
  check("4 显式 windowsHide:false（SW_HIDE 会造出看不见窗口）", /windowsHide:\s*false/.test(blk), true);
  check("5 verbatim 参数（Node 别把整串再包一层引号）", /windowsVerbatimArguments:\s*true/.test(blk), true);
  check("6 兜底仍吞掉 Explorer 的 exit 1", /error\.code !== 1/.test(blk), true);
  check("7 其它平台分支没被牵连（finder 仍 open -R）", src.includes('await run("open", ["-R", path], signal)'), true);
  check("8 runNativeCommand 默认仍 windowsHide=true（powershell 不该弹黑框）",
    /windowsHide: options\.windowsHide \?\? true/.test(src), true);
  check("9 语法可解析", (() => { try { execFileSync(process.execPath, ["--check", TARGET]); return true; } catch { return false; } })(), true);

  if (!LIVE) { console.log("\n（--no-live：静态 " + pass + " 条已过，实跑未做）"); process.exit(fail ? 1 : 0); }

  const cases = [["ASCII", "plain-out", "a.mp4"], ["中文", "中文输出", "成片.mp4"], ["带空格", "with space", "final-8.5s.mp4"]];
  fs.rmSync(ROOT, { recursive: true, force: true });
  for (const [, dir, f] of cases) { const d = path.join(ROOT, dir); fs.mkdirSync(d, { recursive: true }); fs.writeFileSync(path.join(d, f), "x"); }
  const mod = await import(require("url").pathToFileURL(TARGET).href);
  for (const [name, dir, f] of cases) {
    // Explorer 的 LocationURL 会把空格写成 %20（中文保持原样），过滤器要按编码后的形匹配
    const urlLike = "file:///" + path.join(ROOT, dir).replaceAll("\\", "/").replaceAll(" ", "%20") + "*";
    closeUrl(urlLike); await wait(1200);
    await mod.revealNativePath(path.join(ROOT, dir, f), AbortSignal.timeout(15000));
    await wait(3200);
    const c = countVisible(urlLike);
    check("LIVE " + name + " ⇒ 目录窗口可见", c.visible >= 1, true);
    closeUrl(urlLike); await wait(700);
  }
  closeUrl("file:///" + ROOT.replaceAll("\\", "/") + "*");
  fs.rmSync(ROOT, { recursive: true, force: true });
  check("LIVE 临时目录已回收", fs.existsSync(ROOT), false);
  console.log(`\n=== 显示在文件管理器闸：${pass + fail} 条断言，失败 ${fail} ===`);
  process.exit(fail ? 1 : 0);
})();

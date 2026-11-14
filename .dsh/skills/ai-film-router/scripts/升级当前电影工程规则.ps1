param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectPath
)

$ErrorActionPreference = "Stop"

$resolvedProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$projectControlPath = Join-Path $resolvedProjectPath "00_project"
$skillRoot = Split-Path -Parent $PSScriptRoot
$ruleRoot = Join-Path $skillRoot "references\project-rules"

if (-not (Test-Path -LiteralPath $projectControlPath)) {
    throw "这不是电影项目文件夹，缺少：$projectControlPath"
}

$templates = @{
    Agents = Join-Path $ruleRoot "AGENTS.md"
    Routing = Join-Path $ruleRoot "routing.md"
    Status = Join-Path $ruleRoot "project-status.md"
    Reference = Join-Path $ruleRoot "reference-memory.md"
    Checkpoint = Join-Path $ruleRoot "session-checkpoint.md"
    Registry = Join-Path $ruleRoot "artifact-registry.md"
    State = Join-Path $ruleRoot "artifact-state.json"
    SceneRegistry = Join-Path $ruleRoot "scene-registry-template.md"
    ReferenceApplication = Join-Path $ruleRoot "reference-application-template.md"
}

foreach ($template in $templates.Values) {
    if (-not (Test-Path -LiteralPath $template)) {
        throw "新版总控规则不完整，缺少：$template"
    }
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = Join-Path $projectControlPath ("rule-backup-" + $stamp)
New-Item -ItemType Directory -Path $backupPath -Force | Out-Null

$projectFiles = @{
    Agents = Join-Path $resolvedProjectPath "AGENTS.md"
    Routing = Join-Path $projectControlPath "routing.md"
    Status = Join-Path $projectControlPath "project-status.md"
    Reference = Join-Path $projectControlPath "reference-memory.md"
    Checkpoint = Join-Path $projectControlPath "session-checkpoint.md"
    Registry = Join-Path $projectControlPath "artifact-registry.md"
    State = Join-Path $projectControlPath "artifact-state.json"
    SceneRegistry = Join-Path $projectControlPath "scene-registry-template.md"
    ReferenceApplication = Join-Path $projectControlPath "reference-application-template.md"
}

foreach ($existing in $projectFiles.Values) {
    if (Test-Path -LiteralPath $existing) {
        Copy-Item -LiteralPath $existing -Destination $backupPath -Force
    }
}

Copy-Item -LiteralPath $templates.Agents -Destination $projectFiles.Agents -Force
Copy-Item -LiteralPath $templates.Routing -Destination $projectFiles.Routing -Force

$statusResult = "已保留现有项目状态"
if (-not (Test-Path -LiteralPath $projectFiles.Status)) {
    Copy-Item -LiteralPath $templates.Status -Destination $projectFiles.Status
    $statusResult = "已新增项目状态文件"
}

foreach ($pair in @(
    @($templates.Reference, $projectFiles.Reference),
    @($templates.Checkpoint, $projectFiles.Checkpoint),
    @($templates.SceneRegistry, $projectFiles.SceneRegistry)
)) {
    if (-not (Test-Path -LiteralPath $pair[1])) {
        Copy-Item -LiteralPath $pair[0] -Destination $pair[1]
    }
}
Copy-Item -LiteralPath $templates.SceneRegistry -Destination $projectFiles.SceneRegistry -Force
Copy-Item -LiteralPath $templates.ReferenceApplication -Destination $projectFiles.ReferenceApplication -Force

if ((Get-Content -LiteralPath $projectFiles.Reference -Raw -Encoding utf8) -notmatch "阶段适用图") {
    Add-Content -LiteralPath $projectFiles.Reference -Encoding utf8 -Value @"

## V3.3 参考应用兼容规则

- 旧条目没有“阶段适用图”时，系统按方法内容自动补齐：人物与关系检查 02/05，世界规则检查 03/05，结构、场次、动作与对白按实际检查 01/04/05。
- 02、03、05 使用参考方法时，必须另存 `00_project/reference-applications/<阶段>-<成果版本>.md`，写出实际落点；“已读取”不能冒充“已融合”。
- 用户本轮要求和最新已确认项目事实始终高于参考方法；冲突时标记待复核，不静默覆盖。
"@
}

$registryResult = "现有成果登记已经是 V3.3"
if (-not (Test-Path -LiteralPath $projectFiles.Registry)) {
    Copy-Item -LiteralPath $templates.Registry -Destination $projectFiles.Registry
    $registryResult = "已新增 V3.3 成果登记表"
} else {
    $legacyRegistry = Get-Content -LiteralPath $projectFiles.Registry -Raw -Encoding utf8
    if ($legacyRegistry -notmatch "登记表版本：V3\.3") {
        $newRegistry = Get-Content -LiteralPath $templates.Registry -Raw -Encoding utf8
        $migrationBlock = @"

## 旧版登记内容（待核对迁移）

升级脚本保留了下面的旧登记内容。继续创作前，Agent 必须结合实际文件列出迁移清单，让用户确认哪些版本仍是当前有效稿；确认后再写入 V3.3 状态，不能自行猜测。

~~~text
$legacyRegistry
~~~
"@
        Set-Content -LiteralPath $projectFiles.Registry -Value ($newRegistry + $migrationBlock) -Encoding utf8
        $registryResult = "已建立 V3.3 登记表并保留旧登记内容，等待确认迁移"
    }
}

$replaceState = -not (Test-Path -LiteralPath $projectFiles.State)
if (-not $replaceState) {
    try {
        $state = Get-Content -LiteralPath $projectFiles.State -Raw -Encoding utf8 | ConvertFrom-Json
        $schemaProperty = $state.PSObject.Properties["schema_version"]
        if ($null -eq $schemaProperty) {
            $state | Add-Member -NotePropertyName "schema_version" -NotePropertyValue "3.3"
            $state | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $projectFiles.State -Encoding utf8
        } elseif ($state.schema_version -ne "3.3") {
            $state.schema_version = "3.3"
            $state | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $projectFiles.State -Encoding utf8
        }
        $replaceState = $false
    } catch {
        $replaceState = $true
    }
}
if ($replaceState) {
    Copy-Item -LiteralPath $templates.State -Destination $projectFiles.State -Force
}

$statusText = Get-Content -LiteralPath $projectFiles.Status -Raw -Encoding utf8
$fields = @(
    "成果版本登记：``00_project/artifact-registry.md``",
    "成果机器状态：``00_project/artifact-state.json``",
    "项目写入锁：空闲 / Agent名称与范围",
    "分镜图绑定状态：未开始 / 缺图 / 已绑定 / 对应关系待确认",
    "场景空间图：未开始 / 已建立",
    "视觉连续性质检：未开始 / 通过 / 有动机换轴 / 证据不足 / 疑似无过渡跳轴"
)
$managedLabels = @($fields | ForEach-Object { $_.Split('：')[0] }) + "时长预算来源"
$seenLabels = @{}
$headerSeen = $false
$normalizedLines = foreach ($line in ($statusText -split "`r?`n")) {
    if ($line -eq "## V3.3 版本状态") {
        if ($headerSeen) { continue }
        $headerSeen = $true
    }
    $label = $managedLabels | Where-Object { $line.StartsWith("$_：") } | Select-Object -First 1
    if ($label) {
        if ($seenLabels.ContainsKey($label)) { continue }
        $seenLabels[$label] = $true
    }
    $line
}
$normalizedStatus = ($normalizedLines -join "`r`n").TrimEnd() + "`r`n"
$missing = $fields | Where-Object { -not $seenLabels.ContainsKey($_.Split('：')[0]) }
if ($missing.Count -gt 0) {
    $statusHeader = if ($headerSeen) { "" } else { "`r`n## V3.3 版本状态`r`n" }
    $normalizedStatus += $statusHeader + ($missing -join "`r`n") + "`r`n"
}
$timingLine = (Get-Content -LiteralPath $templates.Status -Encoding utf8 | Where-Object { $_ -like "时长预算来源：*" } | Select-Object -First 1)
if ($timingLine) {
    if ($normalizedStatus -match "(?m)^时长预算来源：.*$") {
        $normalizedStatus = [regex]::Replace($normalizedStatus, "(?m)^时长预算来源：.*$", $timingLine)
    } else {
        $normalizedStatus += $timingLine + "`r`n"
    }
}
Set-Content -LiteralPath $projectFiles.Status -Value $normalizedStatus -Encoding utf8

New-Item -ItemType Directory -Path (Join-Path $resolvedProjectPath "05_shots") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $resolvedProjectPath "06_prompts") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $projectControlPath "reference-applications") -Force | Out-Null

$logPath = Join-Path $projectControlPath "rule-upgrade-log.md"
@"
# 规则升级记录

升级时间：$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
升级内容：V3.3 参考方法应用单、04预算到05成稿再到07表演和08镜头的时长复核链、逐镜参考图绑定、空间轴线图、视觉连续性质检、低打断专家路由与成果版本守卫
已备份：$backupPath
成果登记：$registryResult
项目状态：$statusResult
未修改：故事、角色、剧本、资产、表演、镜头、提示词和历史版本
"@ | Set-Content -LiteralPath $logPath -Encoding utf8

Write-Output "旧电影工程规则升级完成：$resolvedProjectPath"
Write-Output "规则备份位置：$backupPath"
Write-Output $registryResult
Write-Output "未修改：所有创作成果与项目历史"

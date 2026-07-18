# install_skills.ps1
# 将本目录下每个含 SKILL.md 的子目录安装到当前 Codex 工作区的
# .agents/skills/<skill>。默认使用 junction，源目录继续作为唯一真相源；
# junction 不可用时回退为复制。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File install_skills.ps1
#   powershell -ExecutionPolicy Bypass -File install_skills.ps1 -Copy
#   powershell -ExecutionPolicy Bypass -File install_skills.ps1 -WorkspaceRoot C:\Trading_analysis

param(
    [switch]$Copy,
    [string]$WorkspaceRoot = "C:\Trading_analysis"
)

$ErrorActionPreference = "Stop"
$srcRoot = $PSScriptRoot
$workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot)
$dstRoot = Join-Path $workspace ".agents\skills"
$expected = @("bottom-fishing", "stock-diagnostic", "weekly-ashare-rank")

if (-not (Test-Path -LiteralPath $workspace -PathType Container)) {
    throw "Codex 工作区不存在：$workspace"
}
New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null

$skills = Get-ChildItem -LiteralPath $srcRoot -Directory |
    Where-Object { $_.Name -in $expected -and (Test-Path -LiteralPath (Join-Path $_.FullName "SKILL.md")) }

$found = @($skills | ForEach-Object { $_.Name })
$missing = @($expected | Where-Object { $_ -notin $found })
if ($missing.Count -gt 0) {
    throw "缺少必须的 skill：$($missing -join ', ')"
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
foreach ($s in $skills) {
    $name = $s.Name
    $src = $s.FullName
    $dst = Join-Path $dstRoot $name

    if (Test-Path -LiteralPath $dst) {
        $item = Get-Item -LiteralPath $dst -Force
        if ($item.LinkType) {
            Remove-Item -LiteralPath $dst -Force
        } else {
            $backup = "$dst.backup-$stamp"
            Move-Item -LiteralPath $dst -Destination $backup
            Write-Host "[backup] $name  ->  $backup"
        }
    }

    $linked = $false
    if (-not $Copy) {
        try {
            New-Item -ItemType Junction -Path $dst -Target $src -ErrorAction Stop | Out-Null
            $linked = $true
            Write-Host "[junction] $name  ->  $src"
        } catch {
            Write-Host "[warn] junction 创建失败，回退为复制：$name"
        }
    }
    if (-not $linked) {
        Copy-Item -LiteralPath $src -Destination $dst -Recurse -Force
        Write-Host "[copy] $name  ->  $dst"
    }
}

Write-Host ""
Write-Host "Codex skills 已安装到：$dstRoot"
foreach ($name in $expected) {
    Write-Host "  /skills -> $name    或    `$$name"
}
Write-Host ""
Write-Host "请重启 Codex 或开启位于 $workspace 的新对话，使 skill 重新发现。"

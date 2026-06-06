# install_skills.ps1
# 把本目录下每个 <skill>/SKILL.md 映射进 ~/.claude/skills/<skill>，
# 使任意新 Claude Code 对话都能 /<skill> 调用。
# 优先用目录 junction（软链接，源改即生效）；失败则回退为复制。
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File install_skills.ps1
#   powershell -ExecutionPolicy Bypass -File install_skills.ps1 -Copy   # 强制复制而非链接

param([switch]$Copy)

$ErrorActionPreference = "Stop"
$srcRoot = $PSScriptRoot
$dstRoot = Join-Path $env:USERPROFILE ".claude\skills"

if (-not (Test-Path $dstRoot)) {
    New-Item -ItemType Directory -Force -Path $dstRoot | Out-Null
    Write-Host "已创建 $dstRoot"
}

# 找出所有含 SKILL.md 的子目录
$skills = Get-ChildItem -Path $srcRoot -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName "SKILL.md") }

if (-not $skills) {
    Write-Host "未在 $srcRoot 找到任何含 SKILL.md 的 skill 目录。"
    exit 0
}

foreach ($s in $skills) {
    $name = $s.Name
    $src  = $s.FullName
    $dst  = Join-Path $dstRoot $name

    # 清掉旧的目标（链接或目录）
    if (Test-Path $dst) {
        $item = Get-Item $dst -Force
        if ($item.LinkType) {
            # 是链接：删链接本身（不动源）
            cmd /c rmdir "$dst" | Out-Null
        } else {
            Remove-Item -Recurse -Force $dst
        }
    }

    $linked = $false
    if (-not $Copy) {
        try {
            New-Item -ItemType Junction -Path $dst -Target $src -ErrorAction Stop | Out-Null
            $linked = $true
            Write-Host "[junction] $name  ->  $src"
        } catch {
            Write-Host "[warn] junction 失败，改为复制: $name"
        }
    }
    if (-not $linked) {
        Copy-Item -Recurse -Force $src $dst
        Write-Host "[copy] $name  ->  $dst"
    }
}

Write-Host ""
Write-Host "完成。已安装 skill:"
Get-ChildItem $dstRoot | ForEach-Object { Write-Host "  /$($_.Name)" }
Write-Host ""
Write-Host "重启 Claude Code 或开新对话，输入 / 即可看到这些 skill。"

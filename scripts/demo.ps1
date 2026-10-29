# AshareSignal Lab 一键演示（保留已有数据；-FreshDemo 创建独立新库）
[CmdletBinding()]
param(
    [switch]$FreshDemo,
    [switch]$SkipSync,
    [switch]$NoServe,
    [ValidateRange(1024, 65535)][int]$Port = 8300
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program 执行失败（退出码 $LASTEXITCODE）；启动已停止。"
    }
}

$node = (Get-Command node -ErrorAction Stop).Source
$tsc = Join-Path $root "apps/web/node_modules/typescript/bin/tsc"
$vite = Join-Path $root "apps/web/node_modules/vite/bin/vite.js"
$npm = Get-Command npm -ErrorAction SilentlyContinue
if ((-not (Test-Path $tsc) -or -not (Test-Path $vite)) -and -not $npm) {
    throw "前端依赖未安装且找不到 npm。请安装含 npm 的 Node.js 20+ 后重试。"
}

Write-Host "[1/4] 检查项目环境..." -ForegroundColor Cyan
if (-not $SkipSync) {
    $uv = (Get-Command uv -ErrorAction Stop).Source
    Invoke-Checked $uv @("--cache-dir", (Join-Path $root ".uv-cache"), "sync", "--frozen")
}
$python = Join-Path $root ".venv/Scripts/python.exe"
Invoke-Checked $python @("-c", "import asg_api.main, asg_pipeline; print('项目模块导入正常')")

Write-Host "[2/4] 构建前端工作台..." -ForegroundColor Cyan
Push-Location (Join-Path $root "apps/web")
try {
    if (-not (Test-Path $tsc) -or -not (Test-Path $vite)) {
        Invoke-Checked $npm.Source @("ci")
    }
    # 已装依赖时直接调用本地工具，兼容仅有 node、没有全局 npm 的环境。
    Invoke-Checked $node @($tsc, "-b")
    Invoke-Checked $node @($vite, "build")
} finally {
    Pop-Location
}

if ($FreshDemo) {
    $demoName = "session-" + [guid]::NewGuid().ToString("N") + ".db"
    $env:ASG_DATABASE_URL = "sqlite:///./data/demo/$demoName"
} elseif (-not $env:ASG_DATABASE_URL) {
    $env:ASG_DATABASE_URL = "sqlite:///./data/demo/ashare_lab.db"
}
Write-Host "[3/4] 准备合成演示数据（复用已绑定库，不删除旧库）..." -ForegroundColor Cyan
Invoke-Checked $python @("scripts/prepare_demo.py")

if ($NoServe) {
    Write-Host "准备完成；未启动服务。" -ForegroundColor Green
    return
}

Write-Host "[4/4] 启动 API + 工作台: http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "      演示账号: admin / researcher / annotator_a / viewer，密码 Demo12345!"
Invoke-Checked $python @("-m", "uvicorn", "asg_api.main:app", "--host", "127.0.0.1", "--port", "$Port")

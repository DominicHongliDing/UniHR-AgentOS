$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendRoot = Join-Path $projectRoot "backend"
$frontendRoot = Join-Path $projectRoot "frontend"
$python = Get-Command python -ErrorAction Stop
$npm = Get-Command npm -ErrorAction Stop
$docker = Get-Command docker -ErrorAction Stop

if (-not (Test-Path -LiteralPath (Join-Path $projectRoot ".env"))) {
    Copy-Item -LiteralPath (Join-Path $projectRoot ".env.example") -Destination (Join-Path $projectRoot ".env")
    Write-Host "[UniHR] 已创建 .env。请填写 UNIHR_BAILIAN_API_KEY 后运行 start.ps1。" -ForegroundColor Yellow
}

$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $python.Source -m venv (Join-Path $backendRoot ".venv")
}

& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e "${backendRoot}[dev,feishu]"
Push-Location $frontendRoot
try {
    & $npm.Source ci
} finally {
    Pop-Location
}

New-Item -ItemType Directory -Force -Path (Join-Path $backendRoot "data\knowledge-source") | Out-Null
& $docker.Source compose --env-file (Join-Path $projectRoot ".env") -f (Join-Path $backendRoot "docker-compose.postgres.yml") up -d

Write-Host "[UniHR] 依赖安装完成。配置 .env 中的 API Key，然后运行 .\start.ps1。" -ForegroundColor Green

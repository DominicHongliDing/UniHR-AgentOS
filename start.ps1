$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendRoot = Join-Path $projectRoot "backend"
$frontendRoot = Join-Path $projectRoot "frontend"
$envFile = Join-Path $projectRoot ".env"
$venvPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$logRoot = Join-Path $backendRoot "data\logs"

if (-not (Test-Path -LiteralPath $envFile)) {
    throw "未找到 .env。请先运行 setup.ps1。"
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "后端环境尚未安装。请先运行 setup.ps1。"
}

foreach ($rawLine in Get-Content -LiteralPath $envFile) {
    $line = $rawLine.Trim()
    if (-not $line -or $line.StartsWith("#")) { continue }
    $separator = $line.IndexOf("=")
    if ($separator -le 0) { continue }
    $name = $line.Substring(0, $separator).Trim()
    $value = $line.Substring($separator + 1).Trim().Trim('"').Trim("'")
    Set-Item -Path "Env:$name" -Value $value
}

if (-not $env:UNIHR_BAILIAN_API_KEY -or $env:UNIHR_BAILIAN_API_KEY -like "replace-*") {
    throw "请先在 .env 中填写 UNIHR_BAILIAN_API_KEY。"
}

New-Item -ItemType Directory -Force -Path $logRoot,(Join-Path $backendRoot "data\knowledge-source") | Out-Null
docker compose --env-file $envFile -f (Join-Path $backendRoot "docker-compose.postgres.yml") up -d | Out-Null

try {
    $health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8088/health" -TimeoutSec 1
    $backendReady = $health.StatusCode -eq 200
} catch {
    $backendReady = $false
}
if (-not $backendReady) {
    Start-Process -FilePath $venvPython -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8088") -WorkingDirectory $backendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot "backend.out.log") -RedirectStandardError (Join-Path $logRoot "backend.err.log")
}

try {
    $web = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:5180" -TimeoutSec 1
    $frontendReady = $web.StatusCode -eq 200
} catch {
    $frontendReady = $false
}
if (-not $frontendReady) {
    Start-Process -FilePath "npm.cmd" -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", "5180", "--strictPort") -WorkingDirectory $frontendRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logRoot "frontend.out.log") -RedirectStandardError (Join-Path $logRoot "frontend.err.log")
}

for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:5180" -TimeoutSec 1
        if ($response.StatusCode -eq 200) {
            Start-Process "http://127.0.0.1:5180"
            Write-Host "[UniHR] 已启动：http://127.0.0.1:5180" -ForegroundColor Green
            exit 0
        }
    } catch {}
    Start-Sleep -Milliseconds 500
}
throw "前端未在预期时间内启动，请查看 backend/data/logs。"


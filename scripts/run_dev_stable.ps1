param(
    [string]$ConfigPath = "configs/config.dev-stable.yaml",
    [string]$BootstrapPython = "C:\\Python312\\python.exe",
    [string]$RedisServerPath = "D:\\vscode\\myredis\\redis-cli\\redis-server.exe",
    [string]$RedisPort = "6381",
    [switch]$InstallLocalAIDeps
)

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force logs | Out-Null

function Start-LocalRedis {
    param(
        [string]$BinaryPath,
        [string]$Port
    )

    if (-not (Test-Path $BinaryPath)) {
        Write-Warning "Redis binary not found: $BinaryPath"
        return
    }

    $tcp = Get-NetTCPConnection -LocalPort ([int]$Port) -State Listen -ErrorAction SilentlyContinue
    if ($tcp) {
        Write-Host "Redis already listening on $Port"
        return
    }

    Write-Host "Starting local Redis on port $Port ..."
    Start-Process -FilePath $BinaryPath `
        -ArgumentList @('--port', $Port, '--requirepass', 'PaiSmart2025', '--appendonly', 'yes') `
        -RedirectStandardOutput (Join-Path (Get-Location) 'logs/devstable-redis.out.log') `
        -RedirectStandardError (Join-Path (Get-Location) 'logs/devstable-redis.err.log')
}

function Start-BackgroundProcess {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$StdOut,
        [string]$StdErr
    )

    Start-Process -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory (Get-Location) `
        -RedirectStandardOutput $StdOut `
        -RedirectStandardError $StdErr
}

Start-LocalRedis -BinaryPath $RedisServerPath -Port $RedisPort

$embeddingArgs = @(
    '-ExecutionPolicy', 'Bypass',
    '-File', 'scripts/start_embedding.ps1',
    '-BootstrapPython', $BootstrapPython,
    '-VenvDir', '.venv-local-ai-cpython',
    '-Model', 'BAAI/bge-small-zh-v1.5',
    '-Port', '18009',
    '-Threads', '2',
    '-Dimensions', '512'
)
if ($InstallLocalAIDeps) {
    $embeddingArgs += '-InstallDeps'
}
Start-BackgroundProcess `
    -FilePath 'powershell' `
    -ArgumentList $embeddingArgs `
    -StdOut (Join-Path (Get-Location) 'logs/devstable-embedding.out.log') `
    -StdErr (Join-Path (Get-Location) 'logs/devstable-embedding.err.log')

$rerankerArgs = @(
    '-ExecutionPolicy', 'Bypass',
    '-File', 'scripts/start_reranker.ps1',
    '-BootstrapPython', $BootstrapPython,
    '-VenvDir', '.venv-local-ai-cpython',
    '-Model', 'BAAI/bge-reranker-base',
    '-Port', '18008'
)
if ($InstallLocalAIDeps) {
    $rerankerArgs += '-InstallDeps'
}
Start-BackgroundProcess `
    -FilePath 'powershell' `
    -ArgumentList $rerankerArgs `
    -StdOut (Join-Path (Get-Location) 'logs/devstable-reranker.out.log') `
    -StdErr (Join-Path (Get-Location) 'logs/devstable-reranker.err.log')

$env:PAISMART_CONFIG = $ConfigPath
Start-BackgroundProcess `
    -FilePath 'go' `
    -ArgumentList @('run', 'cmd/server/main.go') `
    -StdOut (Join-Path (Get-Location) 'logs/devstable-go.out.log') `
    -StdErr (Join-Path (Get-Location) 'logs/devstable-go.err.log')

$env:PAISMART_HOST = '0.0.0.0'
$env:PAISMART_PORT = '8090'
$env:PAISMART_GO_BASE_URL = 'http://127.0.0.1:8081'
$env:PAISMART_INTERNAL_TOKEN = 'paismart-internal-dev'
$env:PAISMART_REQUEST_TIMEOUT_SECONDS = '300'
$env:PAISMART_LLM_BASE_URL = 'https://api.deepseek.com/v1'
$env:PAISMART_LLM_API_KEY = $env:PAISMART_LLM_API_KEY
$env:PAISMART_LLM_MODEL = 'deepseek-chat'
$env:PAISMART_LLM_TEMPERATURE = '0.3'
$env:PAISMART_LLM_TOP_P = '0.9'
$env:PAISMART_LLM_MAX_TOKENS = '2000'
$env:PAISMART_PLANNER_BASE_URL = 'https://api.deepseek.com/v1'
$env:PAISMART_PLANNER_API_KEY = $env:PAISMART_PLANNER_API_KEY
$env:PAISMART_PLANNER_MODEL = 'deepseek-chat'
$env:PAISMART_PLANNER_TEMPERATURE = '0.1'
$env:PAISMART_PLANNER_MAX_TOKENS = '256'
$env:PAISMART_EMBEDDING_BASE_URL = 'http://127.0.0.1:18009'
$env:PAISMART_EMBEDDING_API_KEY = ''
$env:PAISMART_EMBEDDING_MODEL = 'BAAI/bge-small-zh-v1.5'
$env:PAISMART_EMBEDDING_DIMENSIONS = '512'
$env:PAISMART_TIKA_URL = 'http://127.0.0.1:9998'
$env:PAISMART_ES_URL = 'http://127.0.0.1:9200'
$env:PAISMART_ES_USERNAME = ''
$env:PAISMART_ES_PASSWORD = ''

Start-BackgroundProcess `
    -FilePath '.\ai-orchestrator\.venv\Scripts\python.exe' `
    -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--app-dir', 'ai-orchestrator', '--host', '0.0.0.0', '--port', '8090') `
    -StdOut (Join-Path (Get-Location) 'logs/devstable-orchestrator.out.log') `
    -StdErr (Join-Path (Get-Location) 'logs/devstable-orchestrator.err.log')

Write-Host "dev-stable services started."
Write-Host "Config file: $ConfigPath"
Write-Host "Redis:     127.0.0.1:$RedisPort"
Write-Host "Embedding: http://127.0.0.1:18009/health"
Write-Host "Reranker:  http://127.0.0.1:18008/health"
Write-Host "Go API:    http://127.0.0.1:8081/healthz"
Write-Host "AI Orchestrator: http://127.0.0.1:8090/healthz"

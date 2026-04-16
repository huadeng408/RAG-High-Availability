# Boot the local embedding service inside an isolated virtual environment.
param(
    [string]$BootstrapPython = "C:\\Python312\\python.exe",
    [string]$VenvDir = ".venv-local-ai-cpython",
    [string]$Model = "BAAI/bge-small-zh-v1.5",
    [int]$Port = 18009,
    [int]$Threads = 2,
    [int]$Dimensions = 512,
    [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $VenvDir)) {
    & $BootstrapPython -m venv $VenvDir
}

$python = Join-Path $VenvDir "Scripts\\python.exe"

if ($InstallDeps) {
    & $python -m pip install --upgrade pip
    & $python -m pip install -r deployments/embedding/requirements.txt
}

$env:EMBEDDING_MODEL = $Model
$env:EMBEDDING_PORT = "$Port"
$env:EMBEDDING_THREADS = "$Threads"
$env:EMBEDDING_OUTPUT_DIMENSIONS = "$Dimensions"
$env:EMBEDDING_PRELOAD = "true"
$env:EMBEDDING_SERIALIZE_REQUESTS = "true"

& $python -m uvicorn embedding_server:app --app-dir scripts --host 127.0.0.1 --port $Port

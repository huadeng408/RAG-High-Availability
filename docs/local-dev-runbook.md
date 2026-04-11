# PaiSmart 本地联调手册

本文档用于在本机拉起 PaiSmart 的完整本地联调环境，包括：

- 基础依赖
- Go 后端
- Python `ai-orchestrator`
- 前端开发服务器
- 联调验证与排查

## 1. 本地端口规划

### 基础依赖

- MySQL: `127.0.0.1:3307`
- Redis: `127.0.0.1:6380`
- MinIO API: `127.0.0.1:9000`
- MinIO Console: `127.0.0.1:9001`
- Tika: `127.0.0.1:9998`
- Kafka: `127.0.0.1:9092`
- Elasticsearch: `127.0.0.1:9200`
- Reranker: `127.0.0.1:8008`
- Embedding: `127.0.0.1:8009`

### 应用服务

- Go 后端: `127.0.0.1:8081`
- Python AI Orchestrator: `127.0.0.1:8090`
- 前端 Vite: `127.0.0.1:5173`

## 2. 本地配置值

当前本地开发默认使用 [config.yaml](/D:/vscode/paismart-go-main/configs/config.yaml)。

关键配置如下：

```yaml
database:
  mysql:
    dsn: "root:PaiSmart2025@tcp(127.0.0.1:3307)/PaiSmart?charset=utf8mb4&parseTime=True&loc=Local"
  redis:
    addr: "127.0.0.1:6380"
    password: "PaiSmart2025"
    db: 0

minio:
  endpoint: "127.0.0.1:9000"
  access_key_id: "minioadmin"
  secret_access_key: "minioadmin"
  bucket_name: "uploads"

tika:
  server_url: "http://127.0.0.1:9998"

elasticsearch:
  addresses: "http://127.0.0.1:9200"
  index_name: "knowledge_base"

kafka:
  brokers: "127.0.0.1:9092"

ai:
  orchestrator:
    enabled: true
    ingestion_enabled: true
    base_url: "http://127.0.0.1:8090"
    timeout_ms: 120000
    ingestion_timeout_ms: 180000
    shared_secret: "paismart-internal-dev"
```

Python `ai-orchestrator` 需要以下环境变量：

```powershell
$env:PAISMART_INTERNAL_TOKEN="paismart-internal-dev"
$env:PAISMART_GO_BASE_URL="http://127.0.0.1:8081"

$env:PAISMART_LLM_BASE_URL="https://api.deepseek.com/v1"
$env:PAISMART_LLM_API_KEY="<你的 LLM Key>"
$env:PAISMART_LLM_MODEL="deepseek-chat"
$env:PAISMART_LLM_TEMPERATURE="0.3"
$env:PAISMART_LLM_TOP_P="0.9"
$env:PAISMART_LLM_MAX_TOKENS="2000"

$env:PAISMART_PLANNER_BASE_URL="http://127.0.0.1:11434/v1"
$env:PAISMART_PLANNER_API_KEY=""
$env:PAISMART_PLANNER_MODEL="qwen3:4b"
$env:PAISMART_PLANNER_TEMPERATURE="0.1"
$env:PAISMART_PLANNER_MAX_TOKENS="256"

$env:PAISMART_EMBEDDING_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:PAISMART_EMBEDDING_API_KEY="<你的 Embedding Key>"
$env:PAISMART_EMBEDDING_MODEL="text-embedding-v4"
$env:PAISMART_EMBEDDING_DIMENSIONS="2048"

$env:PAISMART_TIKA_URL="http://127.0.0.1:9998"
$env:PAISMART_ES_URL="http://127.0.0.1:9200"
$env:PAISMART_ES_USERNAME=""
$env:PAISMART_ES_PASSWORD=""

$env:PAISMART_RETRIEVAL_KNOWLEDGE_TOP_K="8"
$env:PAISMART_RETRIEVAL_MEMORY_TOP_K="4"
$env:PAISMART_RETRIEVAL_CONTEXT_TOP_K="6"
$env:PAISMART_RETRIEVAL_RRF_K="60"
```

如果远程 Embedding Key 不可用，也可以切到本地容器：

```powershell
$env:PAISMART_EMBEDDING_BASE_URL="http://127.0.0.1:8009"
$env:PAISMART_EMBEDDING_API_KEY=""
$env:PAISMART_EMBEDDING_MODEL="jinaai/jina-embeddings-v2-base-zh"
```

## 3. 启动顺序

建议按下面顺序启动：

1. 启动 Docker Desktop
2. 启动基础依赖容器
3. 启动 Go 后端
4. 启动 Python `ai-orchestrator`
5. 启动前端
6. 运行烟测脚本

## 4. 启动命令

### 4.1 启动基础依赖

```powershell
docker compose -f deployments/docker-compose.yaml up -d
```

检查容器状态：

```powershell
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

### 4.2 启动 Go 后端

```powershell
go run cmd/server/main.go
```

健康检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8081/healthz
```

### 4.3 启动 Python AI Orchestrator

```powershell
python -m venv ai-orchestrator\.venv
ai-orchestrator\.venv\Scripts\python.exe -m pip install -r ai-orchestrator\requirements.txt
```

设置环境变量后启动：

```powershell
ai-orchestrator\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir ai-orchestrator --host 0.0.0.0 --port 8090
```

健康检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8090/healthz
```

### 4.4 启动前端

```powershell
cd frontend
pnpm install
pnpm dev
```

前端默认地址：

```text
http://127.0.0.1:5173
```

## 5. 联调验证顺序

### 第一步：基础可用性

- `GET http://127.0.0.1:8081/healthz`
- `GET http://127.0.0.1:8090/healthz`
- `GET http://127.0.0.1:9200`
- `GET http://127.0.0.1:9001`

### 第二步：AI Orchestrator 基础能力

- `POST /v1/ingestion/chunk`
- `POST /v1/chat/stream`
- Go 内部编排接口：
  - `/internal/orchestrator/session`
  - `/internal/orchestrator/prompt-context`
  - `/internal/orchestrator/knowledge-search`
  - `/internal/orchestrator/memory-search`
  - `/internal/orchestrator/rerank-context`

### 第三步：整链路烟测

运行项目自带脚本：

```powershell
python scripts/verify_langgraph_stack.py `
  --go-base-url http://127.0.0.1:8081 `
  --orchestrator-base-url http://127.0.0.1:8090 `
  --internal-token paismart-internal-dev `
  --user-id 1 `
  --username admin `
  --out benchmarks/results/langgraph-stack-smoke.json
```

## 6. 常见排查点

### Docker 相关

- `docker info` 无法连接：先启动 Docker Desktop
- ES 首次启动较慢：等待插件安装完成
- `kafka-init` / `minio-init` 为一次性容器，退出不代表失败

### Go 后端

- 端口 `8081` 被占用：检查是否有旧进程未退出
- MySQL / Redis 连接失败：先确认容器健康状态
- ES 索引初始化失败：检查 `9200` 是否已可访问

### Python Orchestrator

- `/healthz` 正常但聊天失败：优先检查 LLM / Planner / Embedding Key
- ingestion `parse` 失败：检查 Tika 是否可访问
- ingestion `index` 失败：检查 ES 地址与认证配置

### 前端

- 页面打不开：确认 `pnpm dev` 是否成功启动
- API / WS 连接失败：检查 `frontend/.env.test`

## 7. 停止服务

### 停止基础依赖

```powershell
docker compose -f deployments/docker-compose.yaml down
```

### 停止应用进程

- 直接结束启动命令所在终端
- 或在任务管理器 / PowerShell 中结束对应 `go`、`python`、`node` 进程

## 8. 备注

- Go 与 Python 内部调用会透传 `X-Trace-ID`
- 本地联调建议优先确认基础依赖和后端，再启动前端
- 如果只调后端链路，前端不是必须项

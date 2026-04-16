# dev-stable 本地开发模式

`dev-stable` 模式用于降低 Windows + Docker Desktop + WSL 端口代理对本地联调的影响。

核心思路：

- **本地直跑**
  - Go API Service
  - Python AI Orchestrator
  - Redis
  - Embedding Service
  - Reranker Service
- **Docker 保留**
  - MySQL
  - Elasticsearch
  - Kafka
  - MinIO
  - Tika

## 配置文件

本模式使用：

- [configs/config.dev-stable.yaml](/D:/vscode/paismart-go-main/configs/config.dev-stable.yaml)

关键差异：

- Redis 改为 `127.0.0.1:6381`
- Embedding 改为 `http://127.0.0.1:18009`
- Reranker 改为 `http://127.0.0.1:18008`
- 检索索引改为 `knowledge_base_dev_stable`
- 记忆索引改为 `conversation_memory_dev_stable`

## 启动前准备

### 1. 启动保留在 Docker 中的依赖

```powershell
docker compose -f deployments/docker-compose.yaml up -d mysql es kafka zookeeper minio tika
```

### 2. 启动本地 Redis

默认脚本会尝试使用：

```text
D:\vscode\myredis\redis-cli\redis-server.exe
```

如果你的 Redis 路径不同，可以在启动脚本中覆盖。

## 一键启动

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_dev_stable.ps1 -InstallLocalAIDeps
```

脚本会执行以下动作：

- 在本地启动 Redis（端口 `6381`）
- 使用独立虚拟环境启动本地 Embedding 服务（端口 `18009`）
- 使用独立虚拟环境启动本地 Reranker 服务（端口 `18008`）
- 启动 Go API Service
- 启动 Python AI Orchestrator

首次运行会安装本地 AI 依赖到：

```text
.venv-local-ai-cpython
```

## 单独启动本地 AI 服务

### Embedding

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_embedding.ps1 `
  -BootstrapPython C:\Python312\python.exe `
  -VenvDir .venv-local-ai-cpython `
  -Model BAAI/bge-small-zh-v1.5 `
  -Port 18009 `
  -Threads 2 `
  -Dimensions 512 `
  -InstallDeps
```

健康检查：

```text
GET http://127.0.0.1:18009/health
```

### Reranker

```powershell
powershell -ExecutionPolicy Bypass -File scripts/start_reranker.ps1 `
  -BootstrapPython C:\Python312\python.exe `
  -VenvDir .venv-local-ai-cpython `
  -Model BAAI/bge-reranker-base `
  -Port 18008 `
  -InstallDeps
```

健康检查：

```text
GET http://127.0.0.1:18008/health
```

## 当前已验证

本地化方案已经验证通过的部分：

- `C:\Python312\python.exe` 创建独立虚拟环境
- 本地 `onnxruntime` 可正常导入
- 本地 Embedding 服务可启动并通过健康检查
- 本地 Reranker 服务可启动并通过健康检查

## 建议

- 日常改问答、检索、记忆逻辑时优先使用 `dev-stable`
- 只在需要验证完整拓扑或发版前，才切回全 Docker / 全链集成模式

"""Generate and upload 100+ backend / LLM interview-note documents.

The generated files are intentionally kept on disk for later reuse.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import requests


BACKEND_TOPICS = [
    "MySQL 索引失效场景",
    "MySQL 事务隔离级别",
    "MySQL MVCC 原理",
    "Redis 持久化机制",
    "Redis 缓存穿透雪崩击穿",
    "Redis 分布式锁",
    "Kafka 分区与消费组",
    "Kafka Exactly Once 语义",
    "Kafka 重平衡机制",
    "Elasticsearch 倒排索引原理",
    "Elasticsearch 查询优化",
    "限流 熔断 降级 设计",
    "一致性哈希 场景",
    "CAP 与 BASE",
    "分布式事务方案",
    "高并发 秒杀架构",
    "TCP 三次握手与四次挥手",
    "HTTP 1.1 2 3 对比",
    "HTTPS 握手过程",
    "JWT 与 Session 区别",
    "单点登录设计",
    "消息幂等与去重",
    "服务发现与注册中心",
    "灰度发布与回滚",
    "系统可观测性设计",
    "Prometheus 与 Grafana",
    "日志 Trace Metric 关系",
    "高可用架构设计",
    "B 树 B+ 树 区别",
    "数据库主从复制",
    "悲观锁 乐观锁 区别",
    "线程池参数设计",
    "GC 调优思路",
    "Linux IO 多路复用",
    "Nginx 反向代理与负载均衡",
    "WebSocket 原理与场景",
    "Docker 与 Kubernetes 区别",
    "Kubernetes 调度与探针",
    "服务网格基础",
    "微服务拆分原则",
    "DDD 分层设计",
    "接口幂等设计",
    "大文件上传设计",
    "断点续传实现",
    "对象存储 MinIO 场景",
    "搜索与推荐差异",
    "Rerank 在搜索中的作用",
    "混合检索原理",
    "权限下沉到检索层",
    "多租户数据隔离",
]

LLM_TOPICS = [
    "Transformer 基础原理",
    "Self-Attention 计算过程",
    "位置编码作用",
    "预训练与指令微调差异",
    "SFT DPO RLHF 区别",
    "LoRA 原理",
    "量化 Q4 Q8 差异",
    "KV Cache 作用",
    "Prefill 与 Decode 区别",
    "MoE 基本思想",
    "RAG 核心链路",
    "Chunk 策略设计",
    "Embedding 模型选择",
    "Cross Encoder 与 Bi-Encoder",
    "Recall 与 Precision 平衡",
    "Query Rewrite 场景",
    "多路召回融合",
    "RRF 原理",
    "Hallucination 缓解手段",
    "Function Calling 场景",
    "Agent 与 Workflow 区别",
    "LangChain 与 LangGraph 区别",
    "Prompt Engineering 基础",
    "System Prompt 设计",
    "Few-shot 示例作用",
    "Context Window 预算",
    "Long Context 问题",
    "Memory 体系设计",
    "Working Memory 场景",
    "Profile Memory 场景",
    "Long-term Memory 场景",
    "向量数据库选型",
    "Reranker 超时降级",
    "模型评测指标",
    "Groundedness 与 Faithfulness",
    "Tool Use 与 MCP",
    "多 Agent 协同",
    "安全对齐基础",
    "PII 保护与脱敏",
    "模型服务推理优化",
    "Batching 与并发控制",
    "流式输出设计",
    "TTFT 优化",
    "生成长度控制",
    "Agent 失败恢复",
    "Checkpoint 与 Durable Execution",
    "Human in the Loop",
    "AI 可观测性",
    "Prompt 注入防御",
    "模型路由策略",
]


SECTIONS = [
    "核心概念",
    "典型面试问法",
    "标准回答",
    "工程实践",
    "常见追问",
]


@dataclass
class UploadResult:
    file_name: str
    topic: str
    category: str
    file_md5: str
    size_bytes: int
    chunk_status: int
    merge_status: int
    upload_ms: float
    merge_ms: float
    total_ms: float
    ok: bool
    error: str = ""


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = int((len(values) - 1) * p)
    return values[idx]


def ensure_login(base_url: str, username: str, password: str) -> tuple[str, dict]:
    session = requests.Session()
    login = session.post(
        f"{base_url.rstrip('/')}/api/v1/users/login",
        json={"username": username, "password": password},
        timeout=300,
    )
    if login.status_code != 200:
        session.post(
            f"{base_url.rstrip('/')}/api/v1/users/register",
            json={"username": username, "password": password},
            timeout=600,
        )
        login = session.post(
            f"{base_url.rstrip('/')}/api/v1/users/login",
            json={"username": username, "password": password},
            timeout=300,
        )
    login.raise_for_status()
    token = login.json()["data"]["token"]
    me = session.get(
        f"{base_url.rstrip('/')}/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=300,
    )
    me.raise_for_status()
    return token, me.json()["data"]


def build_doc(topic: str, category: str, index: int) -> str:
    random.seed(f"{category}-{topic}-{index}")
    bullets = [
        f"- {topic} 的本质定义是什么，为什么在 {category} 场景下重要。",
        f"- {topic} 在真实系统里的常见瓶颈与失效模式有哪些。",
        f"- 如果面试官追问实现细节，应该先讲原理再讲工程权衡。",
        f"- 建议从架构目标、数据流、边界条件、监控指标四个维度回答。",
        f"- 回答时优先给结论，再给依据，再补典型案例。",
    ]
    random.shuffle(bullets)
    sections = []
    for i, section in enumerate(SECTIONS, start=1):
        sections.append(f"## {i}. {section}\n")
        sections.append(
            f"{topic} 是 {category} 方向常见八股之一。回答这类问题时，要兼顾原理、场景、边界与工程权衡。\n"
        )
        sections.extend(f"{bullet}\n" for bullet in bullets[:3])
        sections.append(
            f"延伸说明：当 {topic} 和高并发、稳定性、性能优化、故障恢复等结合时，重点说明为什么这样设计，而不是只背概念。\n\n"
        )
    return f"# {topic}\n\n分类：{category}\n文档编号：{index}\n\n" + "".join(sections)


def generate_documents(output_dir: Path, total: int) -> list[tuple[Path, str, str]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    docs: list[tuple[Path, str, str]] = []
    topics = [("backend", item) for item in BACKEND_TOPICS] + [("llm", item) for item in LLM_TOPICS]
    idx = 0
    while len(docs) < total:
        category, topic = topics[idx % len(topics)]
        idx += 1
        ext = ".md" if idx % 5 else ".txt"
        file_name = f"{idx:03d}-{category}-{topic.replace(' ', '-').replace('/', '-')}{ext}"
        path = output_dir / file_name
        if not path.exists():
            path.write_text(build_doc(topic, category, idx), encoding="utf-8")
        docs.append((path, topic, category))
    return docs


def upload_one(base_url: str, token: str, path: Path, topic: str, category: str) -> UploadResult:
    headers = {"Authorization": f"Bearer {token}"}
    content = path.read_bytes()
    file_md5 = md5_hex(content)
    t0 = time.perf_counter()
    with path.open("rb") as f:
        upload_start = time.perf_counter()
        chunk_resp = requests.post(
            f"{base_url.rstrip('/')}/api/v1/upload/chunk",
            headers=headers,
            data={
                "fileMd5": file_md5,
                "fileName": path.name,
                "totalSize": str(len(content)),
                "chunkIndex": "0",
                "chunkMd5": file_md5,
                "orgTag": "",
                "isPublic": "false",
            },
            files={"file": (path.name, f, "text/markdown" if path.suffix == ".md" else "text/plain")},
            timeout=300,
        )
    upload_ms = (time.perf_counter() - upload_start) * 1000
    merge_ms = 0.0
    total_ms = (time.perf_counter() - t0) * 1000
    if chunk_resp.status_code != 200:
        return UploadResult(
            file_name=path.name,
            topic=topic,
            category=category,
            file_md5=file_md5,
            size_bytes=len(content),
            chunk_status=chunk_resp.status_code,
            merge_status=0,
            upload_ms=upload_ms,
            merge_ms=0.0,
            total_ms=total_ms,
            ok=False,
            error=chunk_resp.text[:400],
        )

    merge_start = time.perf_counter()
    merge_resp = requests.post(
        f"{base_url.rstrip('/')}/api/v1/upload/merge",
        headers=headers,
        json={"fileMd5": file_md5, "fileName": path.name},
        timeout=300,
    )
    merge_ms = (time.perf_counter() - merge_start) * 1000
    total_ms = (time.perf_counter() - t0) * 1000
    if merge_resp.status_code != 200:
        return UploadResult(
            file_name=path.name,
            topic=topic,
            category=category,
            file_md5=file_md5,
            size_bytes=len(content),
            chunk_status=chunk_resp.status_code,
            merge_status=merge_resp.status_code,
            upload_ms=upload_ms,
            merge_ms=merge_ms,
            total_ms=total_ms,
            ok=False,
            error=merge_resp.text[:400],
        )

    return UploadResult(
        file_name=path.name,
        topic=topic,
        category=category,
        file_md5=file_md5,
        size_bytes=len(content),
        chunk_status=chunk_resp.status_code,
        merge_status=merge_resp.status_code,
        upload_ms=upload_ms,
        merge_ms=merge_ms,
        total_ms=total_ms,
        ok=True,
    )


def summarize(results: list[UploadResult]) -> dict:
    upload_values = [item.upload_ms for item in results]
    merge_values = [item.merge_ms for item in results]
    total_values = [item.total_ms for item in results]
    ok_count = sum(1 for item in results if item.ok)
    return {
        "total_files": len(results),
        "success_count": ok_count,
        "success_rate": round(ok_count / len(results), 4) if results else 0.0,
        "upload_ms": {
            "avg": round(statistics.fmean(upload_values), 2) if upload_values else 0.0,
            "p50": round(percentile(upload_values, 0.50), 2),
            "p95": round(percentile(upload_values, 0.95), 2),
            "max": round(max(upload_values), 2) if upload_values else 0.0,
        },
        "merge_ms": {
            "avg": round(statistics.fmean(merge_values), 2) if merge_values else 0.0,
            "p50": round(percentile(merge_values, 0.50), 2),
            "p95": round(percentile(merge_values, 0.95), 2),
            "max": round(max(merge_values), 2) if merge_values else 0.0,
        },
        "total_ms": {
            "avg": round(statistics.fmean(total_values), 2) if total_values else 0.0,
            "p50": round(percentile(total_values, 0.50), 2),
            "p95": round(percentile(total_values, 0.95), 2),
            "max": round(max(total_values), 2) if total_values else 0.0,
        },
        "total_bytes": sum(item.size_bytes for item in results),
        "categories": {
            "backend": sum(1 for item in results if item.category == "backend"),
            "llm": sum(1 for item in results if item.category == "llm"),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--username", default="upload_bench_user")
    parser.add_argument("--password", default="PaismartUpload!2026")
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", default="benchmarks/results/upload-baguwen-benchmark.json")
    parser.add_argument("--docs-dir", default="benchmarks/datasets/upload_baguwen")
    args = parser.parse_args()

    token, me = ensure_login(args.base_url, args.username, args.password)
    docs = generate_documents(Path(args.docs_dir), args.count)

    started = time.time()
    results: list[UploadResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(upload_one, args.base_url, token, path, topic, category) for path, topic, category in docs]
        for future in concurrent.futures.as_completed(futures):
            results.append(future.result())

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user": {"username": args.username, "id": me.get("id")},
        "docs_dir": str(Path(args.docs_dir).resolve()),
        "concurrency": args.concurrency,
        "elapsed_sec": round(time.time() - started, 2),
        "summary": summarize(results),
        "results": [item.__dict__ for item in sorted(results, key=lambda x: x.file_name)],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()

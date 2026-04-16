"""Benchmark end-to-end ingestion: upload -> merge -> parse/chunk/embed/index -> searchable."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path

import pymysql
import requests


BACKEND_TOPICS = [
    "MySQL 索引失效场景",
    "MySQL MVCC 原理",
    "Redis 持久化机制",
    "Redis 缓存穿透雪崩击穿",
    "Redis 分布式锁",
    "Kafka 分区与消费组",
    "Kafka Exactly Once 语义",
    "Kafka 重平衡机制",
    "Elasticsearch 倒排索引原理",
    "Elasticsearch 查询优化",
    "高并发 秒杀架构",
    "限流 熔断 降级 设计",
    "HTTP 1.1 2 3 对比",
    "HTTPS 握手过程",
    "JWT 与 Session 区别",
    "单点登录设计",
    "大文件上传设计",
    "断点续传实现",
    "对象存储 MinIO 场景",
    "混合检索原理",
    "权限下沉到检索层",
    "多租户数据隔离",
]

LLM_TOPICS = [
    "Transformer 基础原理",
    "Self-Attention 计算过程",
    "SFT DPO RLHF 区别",
    "LoRA 原理",
    "量化 Q4 Q8 差异",
    "RAG 核心链路",
    "Chunk 策略设计",
    "Embedding 模型选择",
    "Cross Encoder 与 Bi-Encoder",
    "Query Rewrite 场景",
    "多路召回融合",
    "RRF 原理",
    "Hallucination 缓解手段",
    "Agent 与 Workflow 区别",
    "LangChain 与 LangGraph 区别",
    "Memory 体系设计",
    "Working Memory 场景",
    "Profile Memory 场景",
    "Long-term Memory 场景",
    "模型评测指标",
    "Groundedness 与 Faithfulness",
    "Prompt 注入防御",
]


@dataclass
class UploadJob:
    path: Path
    topic: str
    category: str
    marker: str


@dataclass
class UploadResult:
    file_name: str
    topic: str
    category: str
    marker: str
    file_md5: str
    size_bytes: int
    upload_ms: float
    merge_ms: float
    accepted_ms: float
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


def build_doc(topic: str, category: str, index: int, marker: str) -> str:
    sections = [
        "核心概念",
        "标准回答",
        "工程实践",
        "常见追问",
    ]
    body = [f"# {topic}\n\n", f"分类：{category}\n", f"文档编号：{index}\n", f"检索标识：{marker}\n\n"]
    for i, section in enumerate(sections, start=1):
        body.append(f"## {i}. {section}\n")
        body.append(f"{topic} 是 {category} 方向常见八股题。\n")
        body.append(f"- 要先解释 {topic} 的核心定义和边界。\n")
        body.append(f"- 再补充 {topic} 在真实系统中的典型瓶颈、故障模式和优化手段。\n")
        body.append(f"- 最后说明为什么在工程实现里要根据业务目标权衡复杂度、吞吐和稳定性。\n\n")
    return "".join(body)


def generate_documents(output_dir: Path, total: int) -> list[UploadJob]:
    output_dir.mkdir(parents=True, exist_ok=True)
    topics = [("backend", item) for item in BACKEND_TOPICS] + [("llm", item) for item in LLM_TOPICS]
    jobs: list[UploadJob] = []
    idx = 0
    while len(jobs) < total:
        category, topic = topics[idx % len(topics)]
        idx += 1
        marker = f"UPLOAD_E2E_DOC_{idx:03d}"
        ext = ".md" if idx % 5 else ".txt"
        safe_topic = topic.replace(" ", "-").replace("/", "-")
        path = output_dir / f"{idx:03d}-{category}-{safe_topic}{ext}"
        if not path.exists():
            path.write_text(build_doc(topic, category, idx, marker), encoding="utf-8")
        jobs.append(UploadJob(path=path, topic=topic, category=category, marker=marker))
    return jobs


def upload_one(base_url: str, token: str, job: UploadJob) -> UploadResult:
    headers = {"Authorization": f"Bearer {token}"}
    content = job.path.read_bytes()
    file_md5 = md5_hex(content)
    upload_start = time.perf_counter()
    with job.path.open("rb") as f:
        chunk_resp = requests.post(
            f"{base_url.rstrip('/')}/api/v1/upload/chunk",
            headers=headers,
            data={
                "fileMd5": file_md5,
                "fileName": job.path.name,
                "totalSize": str(len(content)),
                "chunkIndex": "0",
                "chunkMd5": file_md5,
                "orgTag": "",
                "isPublic": "false",
            },
            files={"file": (job.path.name, f, "text/markdown" if job.path.suffix == ".md" else "text/plain")},
            timeout=300,
        )
    upload_ms = (time.perf_counter() - upload_start) * 1000
    if chunk_resp.status_code != 200:
        return UploadResult(
            file_name=job.path.name,
            topic=job.topic,
            category=job.category,
            marker=job.marker,
            file_md5=file_md5,
            size_bytes=len(content),
            upload_ms=upload_ms,
            merge_ms=0.0,
            accepted_ms=upload_ms,
            ok=False,
            error=chunk_resp.text[:300],
        )

    merge_start = time.perf_counter()
    merge_resp = requests.post(
        f"{base_url.rstrip('/')}/api/v1/upload/merge",
        headers=headers,
        json={"fileMd5": file_md5, "fileName": job.path.name},
        timeout=300,
    )
    merge_ms = (time.perf_counter() - merge_start) * 1000
    accepted_ms = upload_ms + merge_ms
    if merge_resp.status_code != 200:
        return UploadResult(
            file_name=job.path.name,
            topic=job.topic,
            category=job.category,
            marker=job.marker,
            file_md5=file_md5,
            size_bytes=len(content),
            upload_ms=upload_ms,
            merge_ms=merge_ms,
            accepted_ms=accepted_ms,
            ok=False,
            error=merge_resp.text[:300],
        )

    return UploadResult(
        file_name=job.path.name,
        topic=job.topic,
        category=job.category,
        marker=job.marker,
        file_md5=file_md5,
        size_bytes=len(content),
        upload_ms=upload_ms,
        merge_ms=merge_ms,
        accepted_ms=accepted_ms,
        ok=True,
    )


def fetch_pipeline_states(mysql_cfg: dict[str, object], file_md5s: list[str]) -> dict[str, dict[str, str]]:
    if not file_md5s:
        return {}
    placeholders = ",".join(["%s"] * len(file_md5s))
    sql = (
        f"SELECT file_md5, stage, status FROM pipeline_task "
        f"WHERE file_md5 IN ({placeholders}) AND stage IN ('parse','chunk','embed','index')"
    )
    out: dict[str, dict[str, str]] = {file_md5: {} for file_md5 in file_md5s}
    try:
        conn = pymysql.connect(**mysql_cfg)
    except Exception:
        return out
    try:
        with conn.cursor() as cur:
            cur.execute(sql, file_md5s)
            for file_md5, stage, status in cur.fetchall():
                out[str(file_md5)][str(stage)] = str(status)
    except Exception:
        pass
    finally:
        conn.close()
    return out


def fetch_indexed_ids(es_base_url: str, index_name: str, file_md5s: list[str]) -> set[str]:
    if not file_md5s:
        return set()
    ids = [f"{file_md5}_0" for file_md5 in file_md5s]
    resp = requests.post(
        f"{es_base_url.rstrip('/')}/{index_name}/_mget",
        json={"ids": ids},
        timeout=300,
    )
    resp.raise_for_status()
    indexed: set[str] = set()
    for doc in resp.json().get("docs", []):
        if doc.get("found") and isinstance(doc.get("_id"), str):
            indexed.add(doc["_id"].split("_", 1)[0])
    return indexed


def summarize(values: list[float]) -> dict[str, float]:
    return {
        "avg": round(statistics.fmean(values), 2) if values else 0.0,
        "p50": round(percentile(values, 0.50), 2),
        "p95": round(percentile(values, 0.95), 2),
        "max": round(max(values), 2) if values else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--username", default="upload_bench_user")
    parser.add_argument("--password", default="PaismartUpload!2026")
    parser.add_argument("--count", type=int, default=120)
    parser.add_argument("--upload-concurrency", type=int, default=4)
    parser.add_argument("--knowledge-index", default="knowledge_base_e2e_512")
    parser.add_argument("--mysql-host", default="127.0.0.1")
    parser.add_argument("--mysql-port", type=int, default=3307)
    parser.add_argument("--mysql-user", default="root")
    parser.add_argument("--mysql-password", default="PaiSmart2025")
    parser.add_argument("--mysql-db", default="PaiSmart")
    parser.add_argument("--es-base-url", default="http://127.0.0.1:9200")
    parser.add_argument("--poll-interval-sec", type=int, default=5)
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--docs-dir", default="benchmarks/datasets/upload_baguwen_e2e")
    parser.add_argument("--out", default="benchmarks/results/upload-to-searchable-baguwen-benchmark.json")
    args = parser.parse_args()

    token, me = ensure_login(args.base_url, args.username, args.password)
    jobs = generate_documents(Path(args.docs_dir), args.count)

    started = time.time()
    uploads: list[UploadResult] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.upload_concurrency) as executor:
        futures = [executor.submit(upload_one, args.base_url, token, job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            uploads.append(future.result())

    ok_uploads = [item for item in uploads if item.ok]
    upload_map = {item.file_md5: item for item in ok_uploads}
    mysql_cfg = {
        "host": args.mysql_host,
        "port": args.mysql_port,
        "user": args.mysql_user,
        "password": args.mysql_password,
        "database": args.mysql_db,
        "charset": "utf8mb4",
        "autocommit": False,
        "read_timeout": 300,
        "write_timeout": 300,
        "connect_timeout": 60,
    }

    pending = set(upload_map.keys())
    searchable_at: dict[str, float] = {}
    final_stage_states: dict[str, dict[str, str]] = {}
    deadline = time.time() + args.timeout_sec
    while pending and time.time() < deadline:
        batch = list(pending)
        stage_states = fetch_pipeline_states(mysql_cfg, batch)
        indexed = fetch_indexed_ids(args.es_base_url, args.knowledge_index, batch)
        now = time.time()
        for file_md5 in batch:
            final_stage_states[file_md5] = stage_states.get(file_md5, {})
            if file_md5 in indexed:
                searchable_at[file_md5] = now
                pending.discard(file_md5)
        if pending:
            time.sleep(args.poll_interval_sec)

    rows = []
    searchable_latencies = []
    for item in sorted(uploads, key=lambda x: x.file_name):
        searchable_ms = None
        e2e_ms = None
        if item.file_md5 in searchable_at:
            searchable_ms = round((searchable_at[item.file_md5] - started) * 1000, 2)
            e2e_ms = round(item.accepted_ms + (searchable_at[item.file_md5] - started) * 1000, 2)
            searchable_latencies.append(searchable_ms)
        rows.append(
            {
                **item.__dict__,
                "searchable_ms": searchable_ms,
                "e2e_ms": e2e_ms,
                "pipeline": final_stage_states.get(item.file_md5, {}),
            }
        )

    summary = {
        "total_files": len(uploads),
        "upload_success_count": len(ok_uploads),
        "upload_success_rate": round(len(ok_uploads) / len(uploads), 4) if uploads else 0.0,
        "searchable_count": len(searchable_at),
        "searchable_rate": round(len(searchable_at) / len(ok_uploads), 4) if ok_uploads else 0.0,
        "upload_ms": summarize([item.upload_ms for item in uploads]),
        "merge_ms": summarize([item.merge_ms for item in uploads]),
        "accepted_ms": summarize([item.accepted_ms for item in uploads]),
        "searchable_ms": summarize(searchable_latencies),
        "total_bytes": sum(item.size_bytes for item in uploads),
        "categories": {
            "backend": sum(1 for item in uploads if item.category == "backend"),
            "llm": sum(1 for item in uploads if item.category == "llm"),
        },
    }

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "user": {"username": args.username, "id": me.get("id")},
        "docs_dir": str(Path(args.docs_dir).resolve()),
        "upload_concurrency": args.upload_concurrency,
        "elapsed_sec": round(time.time() - started, 2),
        "summary": summary,
        "results": rows,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()

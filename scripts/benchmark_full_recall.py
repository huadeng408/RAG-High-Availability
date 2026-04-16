"""Benchmark the full recall stack: hybrid knowledge recall, phrase fallback,
memory recall, and cross-encoder rerank.

This script is designed for the local dev-stable topology:
- Go API / internal endpoints on 127.0.0.1:8081
- local reranker on 127.0.0.1:18008
- local embedding on 127.0.0.1:18009

It seeds deterministic benchmark knowledge and memory data, then executes a
multi-stage recall benchmark with configurable concurrency.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pymysql
import requests


INTERNAL_TOKEN = "paismart-internal-dev"


@dataclass
class Scenario:
    name: str
    query: str
    need_history: bool


KNOWLEDGE_DOCS = [
    {
        "file_name": "bench_phrase_doc.md",
        "content": (
            "# Phrase Fallback Benchmark\n\n"
            'Exact phrase validation term: "ZeroTrust Edge Fabric".\n'
            "This document exists to trigger phrase fallback when the quoted phrase is used.\n"
        ),
    },
    {
        "file_name": "bench_hybrid_doc.md",
        "content": (
            "# Hybrid Retrieval Benchmark\n\n"
            "The retrieval chain ends with Cross-Encoder Rerank and Final TopK = 5.\n"
            "The deployment environment is Kubernetes.\n"
            "NebulaMesh is the current project.\n"
        ),
    },
]


MEMORY_DOC = {
    "memory_type": "project",
    "summary": "Current project is NebulaMesh and deployment environment is Kubernetes.",
    "content": "Current project is NebulaMesh. Deployment environment is Kubernetes.",
}


SCENARIOS = [
    Scenario(
        name="phrase_fallback",
        query='[BENCH-PHRASE] Explain the exact phrase "ZeroTrust Edge Fabric".',
        need_history=True,
    ),
    Scenario(
        name="hybrid_retrieval",
        query="[BENCH-HYBRID] What are the last two retrieval steps and what is the deployment environment?",
        need_history=True,
    ),
    Scenario(
        name="memory_fused",
        query="[BENCH-MEMORY] What project is currently deployed on which environment?",
        need_history=True,
    ),
]


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = int((len(values) - 1) * p)
    return values[idx]


def login_or_register(base_url: str, username: str, password: str) -> tuple[str, dict]:
    session = requests.Session()
    login = session.post(
        f"{base_url}/api/v1/users/login",
        json={"username": username, "password": password},
        timeout=300,
    )
    if login.status_code != 200:
        session.post(
            f"{base_url}/api/v1/users/register",
            json={"username": username, "password": password},
            timeout=600,
        )
        login = session.post(
            f"{base_url}/api/v1/users/login",
            json={"username": username, "password": password},
            timeout=300,
        )
    login.raise_for_status()
    token = login.json()["data"]["token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = session.get(f"{base_url}/api/v1/users/me", headers=headers, timeout=300)
    me.raise_for_status()
    return token, me.json()["data"]


def create_embedding(embedding_base_url: str, model: str, text: str, dimensions: int) -> list[float]:
    resp = requests.post(
        f"{embedding_base_url.rstrip('/')}/embeddings",
        json={"model": model, "input": [text], "dimensions": dimensions},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


def seed_knowledge_data(
    mysql_dsn: dict[str, object],
    es_base_url: str,
    index_name: str,
    embedding_base_url: str,
    embedding_model: str,
    embedding_dimensions: int,
    user: dict,
) -> list[str]:
    file_md5s: list[str] = []
    conn = None
    mysql_available = True
    try:
        conn = pymysql.connect(**mysql_dsn)
    except Exception:
        mysql_available = False

    try:
        for doc in KNOWLEDGE_DOCS:
            content = doc["content"]
            file_md5 = md5_hex(content.encode("utf-8"))
            file_md5s.append(file_md5)
            if mysql_available and conn is not None:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO file_upload (file_md5, file_name, total_size, status, user_id, org_tag, is_public)
                        VALUES (%s, %s, %s, 1, %s, %s, 0)
                        ON DUPLICATE KEY UPDATE
                            file_name = VALUES(file_name),
                            total_size = VALUES(total_size),
                            status = 1,
                            org_tag = VALUES(org_tag),
                            is_public = 0
                        """,
                        (
                            file_md5,
                            doc["file_name"],
                            len(content.encode("utf-8")),
                            int(user["id"]),
                            user.get("primaryOrg") or "",
                        ),
                    )
            vector = create_embedding(embedding_base_url, embedding_model, content, embedding_dimensions)
            payload = {
                "vector_id": f"{file_md5}_0",
                "file_md5": file_md5,
                "chunk_id": 0,
                "text_content": content,
                "vector": vector,
                "model_version": embedding_model,
                "user_id": int(user["id"]),
                "org_tag": user.get("primaryOrg") or "",
                "is_public": False,
            }
            resp = requests.put(
                f"{es_base_url.rstrip('/')}/{index_name}/_doc/{file_md5}_0?refresh=true",
                json=payload,
                timeout=300,
            )
            resp.raise_for_status()
        if mysql_available and conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()
    return file_md5s


def seed_memory_data(
    mysql_dsn: dict[str, object],
    es_base_url: str,
    index_name: str,
    embedding_base_url: str,
    embedding_model: str,
    embedding_dimensions: int,
    user: dict,
) -> str:
    memory_id = f"bench-memory-{user['id']}"
    conn = None
    try:
        conn = pymysql.connect(**mysql_dsn)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM long_term_memories WHERE memory_id = %s", (memory_id,))
            cur.execute(
                """
                INSERT INTO long_term_memories
                    (memory_id, user_id, conversation_id, memory_type, content, summary, entities_json, importance)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    memory_id,
                    int(user["id"]),
                    "bench-conversation",
                    MEMORY_DOC["memory_type"],
                    MEMORY_DOC["content"],
                    MEMORY_DOC["summary"],
                    json.dumps(["NebulaMesh", "Kubernetes"], ensure_ascii=False),
                    0.9,
                ),
            )
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()

    vector = create_embedding(
        embedding_base_url,
        embedding_model,
        MEMORY_DOC["summary"],
        embedding_dimensions,
    )
    payload = {
        "memory_id": memory_id,
        "user_id": int(user["id"]),
        "conversation_id": "bench-conversation",
        "memory_type": MEMORY_DOC["memory_type"],
        "text_content": MEMORY_DOC["summary"],
        "vector": vector,
        "importance": 0.9,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    resp = requests.put(
        f"{es_base_url.rstrip('/')}/{index_name}/_doc/{memory_id}?refresh=true",
        json=payload,
        timeout=300,
    )
    resp.raise_for_status()
    return memory_id


def call_knowledge(go_base_url: str, user: dict, query: str) -> dict:
    start = time.perf_counter()
    resp = requests.post(
        f"{go_base_url.rstrip('/')}/internal/orchestrator/knowledge-search",
        headers={"X-Internal-Token": INTERNAL_TOKEN},
        json={
            "user": user,
            "query": query,
            "topK": 10,
            "disableRerank": True,
            "mode": "hybrid",
        },
        timeout=300,
    )
    resp.raise_for_status()
    return {
        "latency_ms": (time.perf_counter() - start) * 1000,
        "body": resp.json()["data"],
    }


def call_memory(go_base_url: str, user: dict, query: str, need_history: bool) -> dict:
    start = time.perf_counter()
    resp = requests.post(
        f"{go_base_url.rstrip('/')}/internal/orchestrator/memory-search",
        headers={"X-Internal-Token": INTERNAL_TOKEN},
        json={
            "user": user,
            "query": query,
            "history": [{"role": "user", "content": "Remember my project context."}],
            "plan": {
                "intent": "follow_up",
                "rewritten_query": query,
                "sub_queries": [],
                "retrieval_mode": "hybrid",
                "need_history": need_history,
                "skip_retrieval": False,
            },
        },
        timeout=300,
    )
    resp.raise_for_status()
    return {
        "latency_ms": (time.perf_counter() - start) * 1000,
        "body": resp.json()["data"],
    }


def call_internal_rerank(go_base_url: str, query: str, docs: list[dict]) -> dict:
    start = time.perf_counter()
    resp = requests.post(
        f"{go_base_url.rstrip('/')}/internal/orchestrator/rerank-context",
        headers={"X-Internal-Token": INTERNAL_TOKEN},
        json={
            "query": query,
            "topK": min(6, len(docs)),
            "items": [
                {
                    "id": item["id"],
                    "sourceType": item["source"],
                    "label": item["label"],
                    "text": item["text"],
                    "score": item["score"],
                }
                for item in docs
            ],
        },
        timeout=300,
    )
    resp.raise_for_status()
    return {
        "latency_ms": (time.perf_counter() - start) * 1000,
        "body": resp.json()["data"],
    }


def call_direct_reranker(reranker_base_url: str, query: str, docs: list[dict]) -> dict:
    start = time.perf_counter()
    resp = requests.post(
        f"{reranker_base_url.rstrip('/')}/rerank",
        json={
            "model": "BAAI/bge-reranker-base",
            "query": query,
            "documents": [item["text"] for item in docs],
            "top_n": min(6, len(docs)),
        },
        timeout=300,
    )
    resp.raise_for_status()
    return {
        "latency_ms": (time.perf_counter() - start) * 1000,
        "body": resp.json(),
    }


def worker(go_base_url: str, reranker_base_url: str, rerank_mode: str, user: dict, scenario: Scenario, run_id: int) -> dict:
    t0 = time.perf_counter()
    knowledge = call_knowledge(go_base_url, user, scenario.query)
    memory = call_memory(go_base_url, user, scenario.query, scenario.need_history)

    docs: list[dict] = []
    for item in knowledge["body"].get("results", []):
        docs.append(
            {
                "id": f"{item.get('fileMd5')}:{item.get('chunkId')}",
                "source": "knowledge",
                "label": item.get("fileName", "knowledge"),
                "text": item.get("textContent", ""),
                "score": float(item.get("score", 0.0)),
            }
        )
    for item in memory["body"].get("items", []):
        docs.append(
            {
                "id": item.get("id", ""),
                "source": "memory",
                "label": item.get("label", "memory"),
                "text": item.get("text", ""),
                "score": float(item.get("score", 0.0)),
            }
        )

    rerank = {"latency_ms": 0.0, "body": {"results": []}}
    if docs:
        if rerank_mode == "direct":
            rerank = call_direct_reranker(reranker_base_url, scenario.query, docs)
        else:
            rerank = call_internal_rerank(go_base_url, scenario.query, docs)

    return {
        "scenario": scenario.name,
        "run_id": run_id,
        "query": scenario.query,
        "knowledge_latency_ms": knowledge["latency_ms"],
        "memory_latency_ms": memory["latency_ms"],
        "rerank_latency_ms": rerank["latency_ms"],
        "total_latency_ms": (time.perf_counter() - t0) * 1000,
        "knowledge_hits": len(knowledge["body"].get("results", [])),
        "memory_hits": len(memory["body"].get("items", [])),
        "rerank_hits": len(rerank["body"].get("items", [])) if rerank_mode != "direct" else len(rerank["body"].get("results", [])),
    }


def summarize(rows: list[dict]) -> dict:
    def metric(name: str) -> dict:
        values = [row[name] for row in rows]
        return {
            "avg": round(statistics.fmean(values), 2) if values else 0.0,
            "p50": round(percentile(values, 0.50), 2),
            "p95": round(percentile(values, 0.95), 2),
            "max": round(max(values), 2) if values else 0.0,
        }

    return {
        "count": len(rows),
        "knowledge_latency_ms": metric("knowledge_latency_ms"),
        "memory_latency_ms": metric("memory_latency_ms"),
        "rerank_latency_ms": metric("rerank_latency_ms"),
        "total_latency_ms": metric("total_latency_ms"),
        "knowledge_hits_avg": round(statistics.fmean([row["knowledge_hits"] for row in rows]), 2) if rows else 0.0,
        "memory_hits_avg": round(statistics.fmean([row["memory_hits"] for row in rows]), 2) if rows else 0.0,
        "rerank_hits_avg": round(statistics.fmean([row["rerank_hits"] for row in rows]), 2) if rows else 0.0,
    }


def parse_mysql_dsn(dsn: str) -> dict[str, object]:
    # root:PaiSmart2025@tcp(127.0.0.1:3307)/PaiSmart?charset=utf8mb4&parseTime=True&loc=Local
    creds, rest = dsn.split("@tcp(")
    user, password = creds.split(":", 1)
    hostport, rest = rest.split(")/")
    host, port = hostport.split(":")
    database = rest.split("?", 1)[0]
    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "database": database,
        "charset": "utf8mb4",
        "autocommit": False,
        "read_timeout": 300,
        "write_timeout": 300,
        "connect_timeout": 60,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--go-base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--api-base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--username", default="e2e_user_c4b8673e")
    parser.add_argument("--password", default="PaismartE2E!2026")
    parser.add_argument("--user-id", type=int, default=0)
    parser.add_argument("--role", default="USER")
    parser.add_argument("--org-tags", default="PRIVATE_6")
    parser.add_argument("--primary-org", default="PRIVATE_6")
    parser.add_argument("--mysql-dsn", default="root:PaiSmart2025@tcp(127.0.0.1:3307)/PaiSmart?charset=utf8mb4&parseTime=True&loc=Local")
    parser.add_argument("--es-base-url", default="http://127.0.0.1:9200")
    parser.add_argument("--knowledge-index", default="knowledge_base_e2e_512")
    parser.add_argument("--memory-index", default="conversation_memory_e2e_512")
    parser.add_argument("--embedding-base-url", default="http://127.0.0.1:18009")
    parser.add_argument("--embedding-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--embedding-dimensions", type=int, default=512)
    parser.add_argument("--reranker-base-url", default="http://127.0.0.1:18008")
    parser.add_argument("--rerank-mode", choices=["internal", "direct"], default="internal")
    parser.add_argument("--skip-seed", action="store_true")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--out", default="benchmarks/results/full-recall-benchmark.json")
    args = parser.parse_args()

    if args.user_id > 0:
        user = {
            "id": int(args.user_id),
            "username": args.username,
            "role": args.role,
            "orgTags": args.org_tags,
            "primaryOrg": args.primary_org,
        }
    else:
        _, me = login_or_register(args.api_base_url.rstrip("/"), args.username, args.password)
        user = {
            "id": int(me["id"]),
            "username": me.get("username", ""),
            "role": me.get("role", "USER"),
            "orgTags": ",".join(me.get("orgTags", [])) if isinstance(me.get("orgTags"), list) else (me.get("orgTags") or ""),
            "primaryOrg": me.get("primaryOrg", ""),
        }

    mysql_cfg = parse_mysql_dsn(args.mysql_dsn)
    if not args.skip_seed:
        seed_knowledge_data(
            mysql_cfg,
            args.es_base_url,
            args.knowledge_index,
            args.embedding_base_url,
            args.embedding_model,
            args.embedding_dimensions,
            user,
        )
        seed_memory_data(
            mysql_cfg,
            args.es_base_url,
            args.memory_index,
            args.embedding_base_url,
            args.embedding_model,
            args.embedding_dimensions,
            user,
        )

    rows: list[dict] = []
    counter = 0
    lock = threading.Lock()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = []
        for _ in range(args.iterations):
            for scenario in SCENARIOS:
                counter += 1
                futures.append(executor.submit(worker, args.go_base_url, args.reranker_base_url, args.rerank_mode, user, scenario, counter))
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            with lock:
                rows.append(row)

    summary = {
        "generated_at": datetime.now().isoformat(),
        "user": user,
        "concurrency": args.concurrency,
        "iterations": args.iterations,
        "scenarios": {
            scenario.name: summarize([row for row in rows if row["scenario"] == scenario.name])
            for scenario in SCENARIOS
        },
        "all": summarize(rows),
        "rows": rows,
        "notes": [
            "knowledge-search uses Go SearchService in hybrid mode, covering BM25 + vector + phrase fallback",
            "memory-search uses Go MemoryService, covering text + vector memory recall",
            "rerank can use the project's /internal/orchestrator/rerank-context endpoint or the local direct cross-encoder service",
            f"seed_skipped={args.skip_seed}",
            "grep Go logs for [BENCH-PHRASE]/[BENCH-HYBRID]/[BENCH-MEMORY] to inspect phraseCandidates/vectorCandidates/bm25Candidates",
        ],
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()

"""Smoke-test the LangGraph/LangChain dual-service stack and write a JSON report."""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
import uuid
from pathlib import Path


def http_json(method: str, url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 30):
    body = None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return resp.getcode(), json.loads(raw.decode("utf-8")), dict(resp.headers.items())


def http_stream_lines(url: str, payload: dict, headers: dict | None = None, timeout: int = 60):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req_headers = {"Content-Type": "application/json", "Accept": "application/x-ndjson"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if line:
                yield line


def collect_stream_preview(url: str, payload: dict, headers: dict | None = None, timeout: int = 60, limit: int = 5):
    lines: list[str] = []
    for line in http_stream_lines(url, payload, headers=headers, timeout=timeout):
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def run_step(name: str, fn):
    start = time.perf_counter()
    try:
        data = fn()
        return {
            "name": name,
            "status": "passed",
            "latencyMs": int((time.perf_counter() - start) * 1000),
            "data": data,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "name": name,
            "status": "failed",
            "latencyMs": int((time.perf_counter() - start) * 1000),
            "error": str(exc),
        }


def skipped_step(name: str, reason: str):
    return {"name": name, "status": "skipped", "reason": reason}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--go-base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--orchestrator-base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--internal-token", default="paismart-internal-dev")
    parser.add_argument("--user-id", type=int, default=0)
    parser.add_argument("--username", default="smoke-user")
    parser.add_argument("--query", default="请概括当前知识库系统的混合检索流程")
    parser.add_argument("--out", default="benchmarks/results/langgraph-stack-smoke.json")
    args = parser.parse_args()

    trace_id = uuid.uuid4().hex[:16]
    headers = {"X-Internal-Token": args.internal_token, "X-Trace-ID": trace_id}

    results: list[dict] = []

    results.append(
        run_step(
            "go_health",
            lambda: http_json("GET", f"{args.go_base_url.rstrip('/')}/healthz", headers={}, timeout=10)[1],
        )
    )
    results.append(
        run_step(
            "orchestrator_health",
            lambda: http_json("GET", f"{args.orchestrator_base_url.rstrip('/')}/healthz", headers={}, timeout=10)[1],
        )
    )
    results.append(
        run_step(
            "ingestion_chunk",
            lambda: http_json(
                "POST",
                f"{args.orchestrator_base_url.rstrip('/')}/v1/ingestion/chunk",
                payload={
                    "task": {
                        "file_md5": "smoke-md5",
                        "file_name": "smoke.txt",
                        "user_id": 1,
                        "stage": "chunk",
                    },
                    "text": "第一段内容。\n\n第二段内容。\n\n第三段内容。",
                    "chunkSize": 16,
                    "chunkOverlap": 4,
                },
                headers=headers,
                timeout=20,
            )[1]["data"],
        )
    )

    if args.user_id <= 0:
        results.append(skipped_step("load_session", "user id not provided"))
        results.append(skipped_step("prompt_context", "user id not provided"))
        results.append(skipped_step("knowledge_search", "user id not provided"))
        results.append(skipped_step("chat_stream", "user id not provided"))
    else:
        session_payload = {"userId": args.user_id}
        results.append(
            run_step(
                "load_session",
                lambda: http_json(
                    "POST",
                    f"{args.go_base_url.rstrip('/')}/internal/orchestrator/session",
                    payload=session_payload,
                    headers=headers,
                    timeout=20,
                )[1]["data"],
            )
        )

        prompt_payload = {
            "user": {
                "id": args.user_id,
                "username": args.username,
                "role": "USER",
                "orgTags": "",
                "primaryOrg": "",
            },
            "plan": {
                "intent": "single_hop",
                "rewritten_query": args.query,
                "sub_queries": [],
                "retrieval_mode": "hybrid",
                "need_history": False,
                "skip_retrieval": False,
            },
        }
        results.append(
            run_step(
                "prompt_context",
                lambda: http_json(
                    "POST",
                    f"{args.go_base_url.rstrip('/')}/internal/orchestrator/prompt-context",
                    payload=prompt_payload,
                    headers=headers,
                    timeout=20,
                )[1]["data"],
            )
        )
        results.append(
            run_step(
                "knowledge_search",
                lambda: http_json(
                    "POST",
                    f"{args.go_base_url.rstrip('/')}/internal/orchestrator/knowledge-search",
                    payload={
                        "user": prompt_payload["user"],
                        "query": args.query,
                        "topK": 5,
                        "disableRerank": True,
                        "mode": "hybrid",
                    },
                    headers=headers,
                    timeout=30,
                )[1]["data"],
            )
        )
        results.append(
            run_step(
                "chat_stream",
                lambda: collect_stream_preview(
                        f"{args.orchestrator_base_url.rstrip('/')}/v1/chat/stream",
                        payload={
                            "query": args.query,
                            "user": {
                                "id": args.user_id,
                                "username": args.username,
                                "role": "USER",
                                "orgTags": "",
                                "primaryOrg": "",
                            },
                        },
                        headers=headers,
                        timeout=90,
                    )
                ,
            )
        )

    summary = {
        "traceId": trace_id,
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": results,
        "passed": len([item for item in results if item["status"] == "passed"]),
        "failed": len([item for item in results if item["status"] == "failed"]),
        "skipped": len([item for item in results if item["status"] == "skipped"]),
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote smoke report to {out_path}")


if __name__ == "__main__":
    main()

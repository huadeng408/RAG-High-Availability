#!/usr/bin/env python3
"""Fail closed on credential formats that must never be committed.

The scanner intentionally checks Git-tracked files only. Local development
passwords and documented placeholders are not treated as provider secrets;
real provider key formats and private-key material are always rejected.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


PROVIDER_PATTERNS = (
    ("OpenAI-compatible key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("private key", re.compile(r"-----BEGIN [A-Z0-9 ]+ PRIVATE KEY-----")),
)
JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^\s*[\"']?(?P<key>password|passwd|pwd|token|api[_-]?key|secret)[\"']?\s*[:=]\s*(?P<quote>[\"'])(?P<value>[^\"']+)(?P=quote)"
)


def _documented_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    return (
        not normalized
        or normalized in {"not-needed", "none", "null"}
        or normalized.startswith(("${", "$env:", "os.getenv(", "getenv(", "<", "replace-", "your-"))
        or normalized.endswith(("_password", "_token", "_secret", "_api_key"))
    )


def _credential_shaped_assignment(key: str, value: str) -> bool:
    if _documented_placeholder(value) or value.startswith("#"):
        return False
    normalized_key = key.lower().replace("-", "_")
    if normalized_key in {"password", "passwd", "pwd"}:
        return len(value) >= 6
    return len(value) >= 12 and any(character.isalpha() for character in value) and any(character.isdigit() for character in value)


def tracked_paths(root: Path) -> list[Path]:
    completed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [root / item for item in completed.stdout.decode("utf-8").split("\0") if item]


def scan(root: Path, paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if path.name == Path(__file__).name:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for label, pattern in PROVIDER_PATTERNS:
            match = pattern.search(content)
            if match:
                line = content.count("\n", 0, match.start()) + 1
                findings.append(f"{path.relative_to(root)}:{line}: {label}")
        jwt_match = JWT_PATTERN.search(content)
        if jwt_match:
            line = content.count("\n", 0, jwt_match.start()) + 1
            findings.append(f"{path.relative_to(root)}:{line}: JWT-like token")
        for match in ASSIGNMENT_PATTERN.finditer(content):
            if not _credential_shaped_assignment(match.group("key"), match.group("value")):
                continue
            line = content.count("\n", 0, match.start()) + 1
            findings.append(f"{path.relative_to(root)}:{line}: hard-coded credential assignment")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked-only", action="store_true", help="scan Git-tracked files (the default)")
    args = parser.parse_args()
    del args
    root = Path(__file__).resolve().parents[1]
    try:
        findings = scan(root, tracked_paths(root))
    except (OSError, subprocess.CalledProcessError) as error:
        print(f"secret scan could not inspect the repository: {error}", file=sys.stderr)
        return 2
    if findings:
        print("Tracked secret-like material detected:", file=sys.stderr)
        print("\n".join(f"- {item}" for item in findings), file=sys.stderr)
        return 1
    print("RHA tracked-secret scan passed: no provider keys or private-key material found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env bash
set -euo pipefail

legacy_camel_prefix='Pai'
legacy_camel="${legacy_camel_prefix}Smart"
legacy_dash_prefix='pai'
legacy_dash="${legacy_dash_prefix}-smart"

matches="$(git grep -Iin -i -e "$legacy_camel" -e "$legacy_dash" -- ':!scripts/verify_rha_naming.sh' || true)"
if [[ -n "$matches" ]]; then
  printf '%s\n' "$matches" >&2
  exit 1
fi

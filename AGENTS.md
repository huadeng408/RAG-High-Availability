# RHA Repository Instructions

## Network

- When external network access fails or is unusually slow, retry the individual command through `http://127.0.0.1:7890`.
- Do not change the Windows system proxy unless explicitly requested.

## Git Delivery

- Direct commits to `main` and pushes to `origin/main` are permitted for meaningful changes after their relevant verification passes.
- Stage only files changed by the active task. Preserve unrelated working-tree changes and generated runtime artifacts.
- Use normal fast-forward pushes. Inspect remote state before any non-fast-forward update; do not force-push unless explicitly requested for that exact remote state.
- Keep credentials and API keys in environment variables. Do not write secrets into tracked files, logs, reports, or commits.

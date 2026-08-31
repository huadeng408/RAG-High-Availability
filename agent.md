# RHA Agent Instructions

## Repository identity

- This repository is RHA. Do not introduce or preserve visible legacy branding in the application.
- Keep the existing 11-node LangGraph graph intact unless the user explicitly changes that requirement.
- Separate fixture acceptance evidence from genuine runtime E2E evidence in reports and final updates.

## Git delivery

- Meaningful verified changes may be committed directly to `main` and pushed to `origin/main`.
- Stage only files changed by the active task. Preserve unrelated working-tree changes and runtime artifacts.
- Inspect the remote state before delivery. Use a normal fast-forward push; never force-push unless explicitly requested for the exact remote state.

## Runtime and network

- Prefer the Windows host workflow for this repository when the project is being demonstrated from Windows.
- Keep credentials and API keys in environment variables or local ignored files.
- If external network access fails, retry the individual command through `http://127.0.0.1:7890` without changing the Windows system proxy.

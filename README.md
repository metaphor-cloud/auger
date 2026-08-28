# reviewrig

A background code review rig. Point it at the directories that hold your git
repositories. It finds every repository, watches them, and reviews the changes with
local large language models. Findings appear in a menu bar application.

Your code stays on your machine. Analysis runs in a container that has the repository
mounted read only and no network at all. The engine reaches your model server through an
allowlist proxy that logs every request.

**Status: early. Nothing works yet. See the milestones below.**

## How it works

Three parts.

- A **menu bar application** built with Tauri. It owns the tray, the window, and the
  lifecycle of the engine.
- An **engine** written in Python. It holds every piece of review logic: discovery,
  policy, scheduling, jobs, retrieval, findings, and tool integration.
- A **sandbox** per job. Apple `container` on macOS 26, else Podman, else Docker, else
  Seatbelt with a warning that the UI keeps showing.

Model servers run on the host, never in the sandbox, for two reasons. A container on
macOS cannot reach Metal or the Apple Neural Engine. And a container with no network at
all cannot leak anything, which is a stronger guarantee than an address allowlist.

Every sandboxed step runs with the repository read only at `/work`, a tmpfs at
`/scratch`, no capabilities, user `nobody`, a memory cap, and a time limit.

## Egress

The engine is the only process that talks to a model, so it is the only process that
could leak code. Its HTTP client refuses any destination that is not on the allowlist
before a byte leaves. Subprocesses, such as a forge command line tool or an MCP server,
run with `HTTPS_PROXY` pointed at an internal proxy that checks the same list and logs
every request with its destination, its size, and its verdict.

```toml
[egress]
allow = ["127.0.0.1:8080"]   # model backends and enabled forges add themselves
```

The System view shows the list, the counts, and anything that was refused.

## Configuration

One file at `~/.reviewrig/config.toml`. Settings merge in three levels: global defaults,
then a forge organisation, then one repository.

```toml
[[roots]]
path = "~/git"
exclude = ["**/node_modules", "~/git/archive/**"]

[defaults]
mode = "draft"                  # off | draft | complete
auto_review_assigned_prs = true
idle_seconds = 300              # wait this long after another agent stops
priority = 5                    # 1 highest, 9 lowest
model_profile = "balanced"

[org."github.com/acme"]
mode = "complete"

[repo."~/git/metaphor/reviewrig"]
priority = 1
hints = """
Treat sandbox escape and egress leaks as critical. Ignore style.
"""
```

`hints` tells the reviewer what matters in that repository. It sets priorities. It does
not control the output format.

## Models

The rig detects a running `llama-server` or `mlx-openai-server`. If it finds none, it
starts a managed `llama.cpp` server and downloads a default model on first use.

A job never names a model. It asks for a job class (`triage`, `review`, `embed`,
`rerank`), and the profile decides the backend. Change a model by editing the profile.

Hosted providers are off by default. Turn one on and your code leaves the machine.

## Other agents

The rig watches for other coding agents in a repository, by process, by git lock, and by
recent writes. It skips a busy repository and waits for the idle timer.

## Development

Requirements: `uv`, `cargo`, `pnpm`, and a container runtime.

```
just setup        # install dependencies
just dev          # run the application against the local engine
just typecheck    # mypy, tsc, cargo check
just test         # pytest, vitest, cargo test
just lint         # ruff, eslint, clippy
just package      # build the .app (a signed .dmg comes at M9)
```

## Milestones

- **M0** Scaffold, build system, tray with a sidecar
- **M1** Discovery, config, policy resolution, the store
- **M2** Sandbox backends and the egress allowlist proxy
- **M3** Model supervisor, gateway, and profiles
- **M4** First real review, end to end
- **M5** Repository map, retrieval, incremental re-index
- **M6** Pull request review for GitHub and GitLab
- **M7** MCP client and per-job tool allowlist
- **M8** Semgrep in the sandbox with model triage
- **M9** Whole repository audits, packaging, docs, release

## Licence

Apache-2.0.

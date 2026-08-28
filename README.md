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

A job never names a model. It asks for a job class (`triage`, `review`, `embed`,
`rerank`), and the profile decides which backend answers. Change a model by editing one
profile line, and nothing else changes.

```toml
[backend.local-review]
url = "http://127.0.0.1:8080/v1"
model = "gpt-oss-120b"
managed = true                     # start it if nothing answers
model_file = "gpt-oss-120b-mxfp4.gguf"

[profile.balanced.review]
backend = "local-review"
max_tokens = 8192
```

The rig prefers a server you already run, because it holds the model you chose and it
may already be warm. It starts one of its own only when nothing answers, and it says
plainly why when it cannot: no server binary, or no weights.

`gpt-oss-120b` in its native MXFP4 form needs about 63 GB and fits in the unified memory
of a workstation. The Q8 form needs about 120 GB and does not.

Hosted providers take two switches: `hosted = true` on the backend, and
`allow_hosted = true` under `[egress]`. Neither alone is enough, because turning one on
sends your code off the machine.

## What the reviewer sees

A diff hides the two things a reviewer needs most: what the changed code is part of, and
who calls it. The rig keeps a code index and answers both.

- tree-sitter maps every file to its symbols, in 19 languages.
- Chunks follow symbol boundaries, so a retrieved piece is a whole function.
- Three searches run together: by overlap with the changed lines, by keyword over the
  changed symbol names, and by meaning over embeddings. A reranker orders the survivors.

Only a file whose git blob sha moved is read again. On this repository, a full index is
145 files and 762 chunks in about 100 ms, and a re-index with no change costs 11 ms.

Search by meaning needs `sqlite-vec` and an embedding backend. Without either, keyword
search still finds callers, and the rig says so in the System view.

## Pull requests

Turn a forge on and the rig reads its open pull requests. By default it reads only the
ones assigned to you or waiting on your review.

```toml
[forge.github]
enabled = true
token_env = "GITHUB_TOKEN"          # falls back to `gh auth token`
```

Two modes decide what happens to the result.

- `draft` writes a review that waits. On GitHub it is a pending review, on GitLab a set
  of draft notes. Nobody but you sees it until you submit it. This is the default.
- `complete` submits. Comments appear on the pull request under your name.

A finding becomes a line comment only when the model was confident and knows the line.
Everything else goes in the summary, marked low confidence, because a wrong comment on
someone else's pull request costs more than a missed one.

The mode is set at any of the three levels, and the narrow level wins, so one repository
can turn off a forge-wide `complete`. The Settings view names every repository that runs
in `complete` mode.

An enabled forge joins the egress allowlist. A forge that is off cannot be reached.

## Your own tools

Attach an MCP server and a review can call it, so the rig works with the pull request
tooling you already use.

```toml
[mcp.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
pass_env = ["GITHUB_PERSONAL_ACCESS_TOKEN"]   # names, never values

[repo."~/git/acme/payments"]
tools = ["github.get_pull_request", "github.list_commits"]
```

Three rules make this safe.

- **Nothing is allowed by default.** A tool runs only when a policy names it, as
  `server.tool` or `server.*`.
- **A server sees only what you named.** It gets `PATH`, `HOME`, and the variables in
  `pass_env`. The engine token and every forge token stay in the engine.
- **Tool output is data.** It is wrapped, labelled, and the prompt says it is never an
  instruction and never changes the output format.

The loop is bounded by `max_tool_calls`, because a model that keeps asking for tools is
a review that never reports.

## Other agents

A review that runs while a coding agent edits the same tree reads a half finished state.
The rig therefore leaves a repository alone when any of these hold:

- A coding agent has its working directory inside it. The check reads the process name,
  the first word of its command line, and the script behind an interpreter, because an
  agent may report a version string as its name or ship as a shell wrapper.
- Git has an operation in flight: `index.lock`, a merge, a rebase, a bisect.
- Something wrote to the tree less than `idle_seconds` ago.

Every skip is recorded with its reason, so a repository that is never reviewed is
visible instead of silently absent. Repeats of the same reason share one row and a
count.

## What a review reads

Git runs on the host with hooks, external diff drivers, textconv, and every config file
turned off, and it never writes to your repository. A `clone` cannot carry a config file,
so a hostile repository has no path from its content to a command through these calls.
Anything that does run repository-provided code, a build, a dependency install, or a
linter that loads repository rules, runs in the sandbox instead.

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

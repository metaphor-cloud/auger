# reviewrig

A background code review rig. Point it at the directories that hold your git
repositories. It finds every repository, watches them, and reviews the changes with
local large language models. Findings appear in a menu bar application.

Your code stays on your machine. Analysis runs in a container that has the repository
mounted read only and **no network at all**. The engine reaches your model server through
an allowlist that logs every request.

[Install](docs/install.md) · [Configuration](docs/configuration.md) ·
[Models](docs/models.md) · [What it can reach](docs/security.md)

## What it does

- Finds every git repository under the roots you name, and keeps watching them.
- Reviews each new commit, and the work you have not committed yet.
- Reviews pull requests on GitHub and GitLab, as a draft that waits for you, or as a
  submitted review if you ask for that.
- Runs Semgrep in the sandbox and asks a model which of its findings are real.
- Audits a whole repository on a slower timer, for the problems a diff cannot show.
- Waits when another coding agent is working in a repository.

## How it works

Three parts.

- A **menu bar application** built with Tauri. It owns the tray, the window, and the
  lifecycle of the engine. It holds no review logic.
- An **engine** in Python. Discovery, policy, scheduling, jobs, retrieval, findings, and
  tools.
- A **sandbox** per job. Apple `container` on macOS 26, else Podman, else Docker, else
  Seatbelt with a warning the UI keeps showing.

Model servers run on the host, never in the sandbox, for two reasons. A container on
macOS cannot reach Metal or the Apple Neural Engine. And a container with no network at
all cannot leak anything, which is a stronger guarantee than an address allowlist.

## The window

**Overview** is the landing page: what the rig is doing now, open findings by severity,
the repositories that need attention, what it has run today and what that cost, and one
banner per thing that needs you. **Findings**, **Repositories**, **Runs**, **Models**,
**Settings**, and **System** are behind it.

## Settings

One file at `~/.reviewrig/config.toml`, and the UI edits the same file without losing
your comments. Settings merge in three levels: everything, one forge organisation, then
one repository. The narrow level wins.

```toml
[[roots]]
path = "~/git"

[defaults]
mode = "draft"                  # off | draft | complete
idle_seconds = 300              # wait this long after another agent stops
priority = 5

[org."github.com/acme"]
mode = "complete"

[repo."~/git/acme/payments"]
priority = 1
hints = """
Treat a leaked credential as critical. Ignore style.
"""
```

Two things steer the reviewer, and the difference is who wrote them. `instructions` are
yours, from your config file, so they are trusted and can change what the rig looks for
and how it judges severity. `hints` live with the repository, so they are treated as data
and only set priorities.

`exclude` drops a repository wherever the roots find it: a path, a glob, or a forge key.
It stays listed and marked excluded, so it is visibly dropped rather than quietly lost.

The full reference is in [docs/configuration.md](docs/configuration.md).

## Models

The rig brings its own. Press Set up in the Models view and it fetches a `llama.cpp`
build for this machine, fetches weights that fit its memory, and starts the servers.
Nothing else to install. Everything it fetches is checked against a published sha256, and
a download that drops carries on from where it stopped.

If you already run a model server, point a backend at it instead and skip all of that.

A job asks for a job class (`review`, `triage`, `embed`, `rerank`) and the profile
decides which backend answers. Changing a model is one line.

Hosted models are off, and they take two switches to turn on, because turning one on
sends your code off the machine. See [docs/models.md](docs/models.md).

## What the reviewer sees

A diff hides what the changed code is part of and who calls it. The rig keeps a
tree-sitter index of every symbol in 19 languages, and answers both with three searches:
by overlap with the changed lines, by keyword over the changed symbol names, and by
meaning over embeddings. A reranker orders the result.

Only a file whose git blob sha moved is read again. On this repository a full index is
145 files and 762 chunks in about 100 ms without embeddings, and a re-index with no
change costs 11 ms.

Which models to use for this was measured, not chosen. Over 25 symbols with references
computed from the syntax tree, keyword search alone reached recall@12 of 0.584 and found
nothing at all for three of them; adding `nomic-embed-code` reached 0.686 and found
something for every one. A reranker made it markedly worse, so the rig does not fetch
one. The numbers are in [docs/models.md](docs/models.md).

## Other agents

A review that runs while a coding agent edits the same tree reads a half finished state.
The rig leaves a repository alone when a coding agent has its working directory inside
it, when git has an operation in flight, or when something wrote to the tree less than
`idle_seconds` ago. Every skip is recorded with its reason, because a repository that is
never reviewed must be visible.

## Your own tools

Attach an MCP server and a review can call it. Nothing is allowed by default: a tool runs
only when a policy names it. A server sees `PATH`, `HOME`, and the variables you list,
never a token the rig holds. Tool output is data, never an instruction.

## Development

Requirements: `uv`, `cargo`, `pnpm`, and a container runtime.

```
just setup        # install dependencies
just dev          # run the application against the engine in this checkout
just build-image  # build the analysis image. Needs network.
just typecheck    # mypy, tsc, cargo clippy
just test         # pytest, vitest, cargo test
just lint         # ruff, eslint, cargo fmt
just package      # build the .app
just release      # build a signed .dmg. Needs your Apple certificate.
```

## Status

This is version 0.1.0. It is early, and this is what has and has not been proved.

**Runs, and was watched running.** Repository discovery, the scheduler, busy detection,
the code index and retrieval, the sandbox rules against a real Apple container, the
egress refusals, the model setup against the real GitHub and Hugging Face hosts, and a
review by a real model that found the planted bug and one that was not planted.

**Tested against a server that speaks the real protocol, but not against the real
service.** The GitHub and GitLab adapters, and the MCP client. Each is exercised over
real HTTP or a real subprocess, against a fixture that implements the same API.

**Not run at all.** Semgrep, because the analysis image needs a network to build and this
machine could not reach one.

**Not signed.** `just package` builds an application that runs where it was built.
Distribution needs an Apple Developer certificate.

## Licence

Apache-2.0.

# Configuration

One file: `~/.auger/config.toml`. The rig writes a starter version on first run.

The UI edits the same file and keeps your comments, so you can use either.

Nothing here is reachable only by editing the file. Settings groups what you reach for
into tabs, and its Everything tab draws a control for every remaining setting from what
the engine says it holds, so a setting added to Auger is one you can change the day it
arrives. A test refuses a new list or table that has no form of its own.

Set `AUGER_HOME` to move the whole directory, which holds the config, the database,
and the model weights.

## Roots

Where to look for git repositories. A directory that holds `.git` is a repository, and
the walk does not descend into it.

```toml
[[roots]]
path = "~/git"
exclude = ["**/node_modules/", "~/git/archive/"]
max_depth = 4
```

| Key | Default | Meaning |
| --- | --- | --- |
| `path` | none | The directory to walk. `~` is expanded. |
| `exclude` | `[]` | gitignore patterns. Relative to the root, or absolute. |
| `max_depth` | unlimited | How deep below the root to walk. |

Dependency directories are excluded already: `node_modules`, `.venv`, `venv`, `vendor`,
`target`, `.cargo`, `Library`, `.Trash`, `.cache`.

## Excluding a repository

`exclude` drops a repository wherever the roots find it. Each entry is a path, a glob, or
a forge key, and a forge key matches on a segment boundary, so `github.com/acme` never
matches `github.com/acmecorp`.

```toml
exclude = [
  "~/git/scratch",
  "~/git/forks/*",
  "github.com/someone-else",
]
```

An excluded repository is still listed, marked excluded, and never reviewed. Use a
`[repo]` section instead when you want to change a setting rather than drop it.

## Call graph

```toml
[codegraph]
enabled = true
```

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Ask CodeGraph for the callers of a changed symbol. |
| `command` | `codegraph` | The program to run. |
| `timeout_seconds` | `20` | How long one lookup may take. |
| `limit` | `20` | Callers to ask for per changed symbol. |

Text search finds a name and vector search finds something similar. Neither knows that
one function calls another, and CodeGraph does. The rig reads an index that is already
there and never creates one, because indexing a repository writes into it. A repository
with no `.codegraph` directory is retrieved the usual way, and the System view says so.

## Policy

The same fields appear at three levels: `[defaults]` for every repository,
`[org."host/name"]` for one forge organisation, and `[repo."/path"]` for one repository.
A narrower level wins. A repository key may be an exact path or a glob.

```toml
[defaults]
mode = "draft"

[org."github.com/acme"]
mode = "complete"

[repo."~/git/acme/payments"]
mode = "off"
hints = "Treat a leaked credential as critical. Ignore style."
```

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Whether the rig touches this repository at all. |
| `mode` | `draft` | `off`, `draft`, or `complete`. See [Pull requests](#pull-requests). |
| `auto_review_assigned_prs` | `true` | Read only the pull requests assigned to you or waiting on your review. |
| `idle_seconds` | `300` | Wait this long after the last write before a review starts. |
| `priority` | `5` | 1 first, 9 last. |
| `model_profile` | `balanced` | Which profile picks the models. |
| `instructions` | `""` | Your own instructions to the reviewer. Trusted, and they can change the rules. |
| `hints` | `""` | Notes that live with the repository. Data, and they only set priorities. |
| `system_prompt` | `""` | The whole system prompt. Empty means the one auger ships. |
| `tools` | `[]` | MCP tools this repository may use, as `server.tool` or `server.*`. |
| `code_tools` | `false` | Let the reviewer read the repository itself: a file, a keyword search, a file's symbols, a function's callers. In process, so a call costs milliseconds. |
| `commands` | `false` | Let the reviewer run commands in the sandbox. Each call starts a container, so a review that loops over them does not finish. |
| `max_tool_calls` | `4` | How many tool calls one review may make. `0` removes the ceiling. |
| `working_set_tokens` | `8192` | How large a prompt one review builds. The model's context only ever lowers it. |
| `audit_hours` | `24` | How often a whole repository audit runs. `0` turns audits off. |
| `adversary` | `false` | Have a second model judge what the first one found. |
| `alternate` | `true` | Swap the two models between runs, so neither decides alone. |

### instructions and hints

Both reach the reviewer, and the difference is who wrote them.

`instructions` come from your config file, so they are yours. They go in the system
message, and they can narrow what is reported, add something to look for, or change how
severity is judged. They cannot change the output format, because the rig has to parse it.

```toml
[defaults]
instructions = """
Report security defects and data loss. Ignore performance unless it is a loop over a
network call. Treat anything that writes a credential to a log as critical.
"""

[repo."~/git/acme/prototype"]
instructions = "This is a prototype. Report only what would lose data."
```

### A second model that argues

`adversary` turns on a second reviewer. After a review, the other model reads the same
change and each finding, and says whether the code shown supports it. A finding it
rejects is marked dismissed rather than deleted: the disagreement is worth seeing, and
the model doing the rejecting is not always right either.

It needs somewhere to run. Point the profile's `verify` class at a second backend, and
choose a model from a different family than the reviewer, because a second opinion is
only worth having when it comes from somewhere else. The Models view offers Qwen3-Coder,
Gemma 3 QAT, and Qwen3-8B for this.

```toml
[profile.balanced.verify]
backend = "local-adversary"
```

With `alternate` on, the two trade places between runs, so neither one's blind spots
decide on their own.

### Where the weights come from

```toml
[models]
token_env = "HF_TOKEN"
```

| Key | Default | Meaning |
| --- | --- | --- |
| `token_env` | `HF_TOKEN` | Name of the variable that holds a Hugging Face token. Never the token. |
| `custom` | `{}` | Models you added by naming a repository and a file. |

The window searches Hugging Face directly, so a repository and a filename are the
advanced path rather than the usual one.

### The system prompt

`system_prompt` is the whole thing the reviewer is told, and it is yours. Settings,
Review edits it directly, and ships six to start from: as it comes, security first,
correctness only, performance, demanding, and one that reports only what you would stop
a release for.

One thing has to survive an edit. The parser reads the answer, so a prompt that stops
asking for `findings`, `severity`, `file`, and `title` gives a review that nothing can
read, and every run records a bad answer. The window says so before you save, and it
saves anyway, because it is your prompt.

`instructions` is separate, and it is what a level adds on top. An organisation or one
repository can add a line without rewriting the whole prompt.

`hints` live with the repository, and a repository you did not write is not you. They go
in the user message, wrapped and labelled as data. They set priorities and they do not
change the rules or the output format.

## Models

A job asks for a job class, never for a model. The profile decides which backend answers.

```toml
image = "ghcr.io/metaphor-cloud/auger:analysis-0.1"

[backend.local-review]
url = "http://127.0.0.1:1337/v1"
model = "gpt-oss-120b"
managed = true
model_file = "gpt-oss-120b-mxfp4.gguf"

[profile.balanced.review]
backend = "local-review"
max_tokens = 8192
```

| Backend key | Default | Meaning |
| --- | --- | --- |
| `url` | `http://127.0.0.1:1337/v1` | An OpenAI-compatible base URL. |
| `model` | `""` | The model name to send. |
| `api_key_env` | none | Name of the variable holding a key. Never the key. |
| `max_concurrent` | `4` | Requests in flight. A batch server stays full at this depth. |
| `context_tokens` | `0` | How large a prompt one request may hold. Zero works it out: the largest size the model was trained for that this machine can also hold, read from the model's own header and its own memory. A number set here is still held to both, because asking for more than either fails the whole server rather than one request. |
| `hosted` | `false` | This backend sends your code off the machine. |
| `managed` | `false` | Start this server when nothing answers at `url`. |
| `engine` | `llama` | Which engine serves a managed backend. `llama` holds the whole model in memory and reads one weights file. `coli` streams a sparse model's experts from disk and reads a directory of them, so a machine can run a model it could not hold; it answers chat only, and needs Python 3. |
| `model_file` | `""` | Weights under `~/.auger/models`. A directory of them, for an engine that reads a directory. |
| `model_url` | `""` | Where to fetch the weights. The repository, for an engine that reads a directory. |
| `args` | `[]` | Extra arguments for a managed server. |

| Profile key | Default | Meaning |
| --- | --- | --- |
| `backend` | none | Which backend answers this job class. |
| `max_tokens` | `4096` | Answer length. |
| `temperature` | `0.1` | How much the model may wander. |

The four job classes are `review`, `triage`, `embed`, and `rerank`.

`image` names the container image that every sandboxed step runs in. Build it with
`just build-image`.

## Egress

```toml
[egress]
allow = ["https://internal.example"]
allow_hosted = false
```

| Key | Default | Meaning |
| --- | --- | --- |
| `allow` | `[]` | Extra destinations. Model backends and enabled forges add themselves. |
| `allow_hosted` | `false` | Permit a backend marked `hosted`. Both switches are needed. |

## Schedule

```toml
[schedule]
quiet_hours = "22:00-07:00"
```

| Key | Default | Meaning |
| --- | --- | --- |
| `max_concurrent_reviews` | `1` | Reviews at once, across every repository. |
| `poll_seconds` | `60` | How often the rig looks for a new commit. |
| `forge_poll_seconds` | `300` | How often it asks the forges for pull requests. |
| `retry_seconds` | `120` | How long to wait before retrying a busy repository. |
| `quiet_hours` | `""` | `HH:MM-HH:MM` in local time. No audit starts inside it. |
| `audit_poll_seconds` | `900` | How often it looks for a repository that is due an audit. |
| `verify_poll_seconds` | `600` | How often the second model is offered the findings nothing has judged. |
| `idle_only` | `false` | Work only while nobody is using the machine. |
| `idle_after_seconds` | `300` | How long the machine has to be left alone to count as idle. |
| `model_poll_seconds` | `60` | How often it checks that the managed models are still running, and starts one that stopped. |

`max_concurrent_reviews` is one for a reason worth reading before raising it. A model
server reuses a slot's key and value cache when a new prompt shares a prefix with what
that slot last held. Two reviews of different repositories share no prefix, so they land
on the slots alternately and each evicts the other's cache. Prompt evaluation is a large
share of a review's wall clock at local speeds, and a cache hit removes most of it.

## Forges

```toml
[forge.github]
enabled = true
token_env = "GITHUB_TOKEN"
```

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `false` | Off until you turn it on. An enabled forge joins the egress allowlist. |
| `kind` | `github` | `github` or `gitlab`. |
| `host` | `github.com` | The host in a git remote, which is how a repository is matched. |
| `api` | `https://api.github.com` | Where the API lives. A self hosted forge changes this and `host`. |
| `token_env` | `GITHUB_TOKEN` | Name of the variable holding the token. |
| `token_command` | `["gh", "auth", "token"]` | Used when the variable is unset. |

## Tools

```toml
[mcp.github]
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
pass_env = ["GITHUB_PERSONAL_ACCESS_TOKEN"]
```

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | Whether this server is attached. |
| `transport` | `stdio` | `stdio` or `http`. |
| `command` | `""` | The program to run, for `stdio`. |
| `args` | `[]` | Its arguments. |
| `pass_env` | `[]` | Names of variables to pass through. Never values. |
| `env` | `{}` | Extra variables set for the server. |
| `url` | `""` | The endpoint, for `http`. |
| `auth` | `none` | `none` or `oauth`, for `http`. |
| `scope` | `""` | What to ask the authorization server for. Empty asks for its default. |
| `callback_port` | `7431` | Where the browser comes back to during a sign in. |
| `timeout_seconds` | `30` | How long one tool call may take. |

An MCP server runs outside the sandbox and speaks for you. Nothing is allowed by default:
a tool runs only when a policy's `tools` names it.

### The reviewer's own tools

Two switches on a policy, both off, and neither needs an MCP server.

`code_tools` lets the reviewer read the repository for itself: a slice of a file, a
keyword search over the index, a file's symbols, a function's callers. They answer in
process out of the index that is already built, so a call costs milliseconds and nothing
is executed.

`commands` lets it run a command in the analysis sandbox. That call starts a container,
which costs seconds, so a review that loops over them takes minutes rather than the two
it takes without. Turn it on for a repository where running something settles a question
reading cannot.

Both are off because retrieval already puts the surrounding code in the prompt before the
model is asked anything, and a loop has to beat that to earn its turns. `max_tool_calls`
bounds the loop whichever is on; `0` removes the bound, and a review with a tool then
need never end.

An `http` server is a destination like any other, so it must be reachable: an enabled
server joins the egress allowlist, and its traffic goes through the same guard as
everything else the engine sends.

### Signing in to an OAuth server

```toml
[mcp.acme]
transport = "http"
url = "https://tools.acme.com/mcp"
auth = "oauth"
```

Press Sign in under Settings, Tools. A browser opens, you approve, and the token is
written to `~/.auger/oauth/<name>.json`, which only you can read. The rig registers
itself with the authorization server on the first sign in.

A background review never opens a browser. It uses the stored token and refreshes it on
its own. When the token is gone or the refresh fails, the run fails and the server shows
`sign in needed`, because a browser window that nobody asked for is worse than a run that
says what it needs.

The sign in obeys the allowlist too. If the authorization server is on another host, add
it to `egress.allow`. The refusal names the host and port to add.

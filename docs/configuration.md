# Configuration

One file: `~/.auger/config.toml`. The rig writes a starter version on first run.

The UI edits the same file and keeps your comments, so you can use either.

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
| `max_tool_calls` | `8` | How many tool calls one review may make. |
| `audit_hours` | `24` | How often a whole repository audit runs. `0` turns audits off. |

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
image = "auger/analysis:0.1"

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
| `hosted` | `false` | This backend sends your code off the machine. |
| `managed` | `false` | Start this server when nothing answers at `url`. |
| `model_file` | `""` | Weights under `~/.auger/models`. |
| `model_url` | `""` | Where to fetch the weights. |
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
max_concurrent_reviews = 2
quiet_hours = "22:00-07:00"
```

| Key | Default | Meaning |
| --- | --- | --- |
| `max_concurrent_reviews` | `2` | Reviews at once, across every repository. |
| `poll_seconds` | `60` | How often the rig looks for a new commit. |
| `forge_poll_seconds` | `300` | How often it asks the forges for pull requests. |
| `retry_seconds` | `120` | How long to wait before retrying a busy repository. |
| `quiet_hours` | `""` | `HH:MM-HH:MM` in local time. No audit starts inside it. |
| `audit_poll_seconds` | `900` | How often it looks for a repository that is due an audit. |
| `model_poll_seconds` | `60` | How often it checks that the managed models are still running, and starts one that stopped. |

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

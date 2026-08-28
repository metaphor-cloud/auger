# Configuration

One file: `~/.reviewrig/config.toml`. The rig writes a starter version on first run.

The UI edits the same file and keeps your comments, so you can use either.

Set `REVIEWRIG_HOME` to move the whole directory, which holds the config, the database,
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
| `hints` | `""` | Free text that tells the reviewer what matters here. |
| `tools` | `[]` | MCP tools this repository may use, as `server.tool` or `server.*`. |
| `max_tool_calls` | `8` | How many tool calls one review may make. |
| `audit_hours` | `24` | How often a whole repository audit runs. `0` turns audits off. |

`hints` goes into the prompt verbatim, wrapped and labelled as the repository owner's
words. It sets priorities. It does not change the rules or the output format.

## Models

A job asks for a job class, never for a model. The profile decides which backend answers.

```toml
image = "reviewrig/analysis:0.1"

[backend.local-review]
url = "http://127.0.0.1:8080/v1"
model = "gpt-oss-120b"
managed = true
model_file = "gpt-oss-120b-mxfp4.gguf"

[profile.balanced.review]
backend = "local-review"
max_tokens = 8192
```

| Backend key | Default | Meaning |
| --- | --- | --- |
| `url` | `http://127.0.0.1:8080/v1` | An OpenAI-compatible base URL. |
| `model` | `""` | The model name to send. |
| `api_key_env` | none | Name of the variable holding a key. Never the key. |
| `max_concurrent` | `4` | Requests in flight. A batch server stays full at this depth. |
| `hosted` | `false` | This backend sends your code off the machine. |
| `managed` | `false` | Start this server when nothing answers at `url`. |
| `model_file` | `""` | Weights under `~/.reviewrig/models`. |
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
| `timeout_seconds` | `30` | How long one tool call may take. |

An MCP server runs outside the sandbox and speaks for you. Nothing is allowed by default:
a tool runs only when a policy's `tools` names it.

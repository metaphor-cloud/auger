# What the rig can reach

The rig reads your code and sends parts of it to a model. This page says exactly where
each part can go.

## A sandboxed step has no network at all

Every analysis step runs in a container. The repository is mounted read only at `/work`,
scratch is a tmpfs at `/scratch`, every capability is dropped, the process is `nobody`,
memory is capped, and the run is killed at its time limit.

There is no network. Not a restricted network: none.

This is stronger than an address allowlist, and it is also the only option that works.
Three facts, measured on macOS 26 with Apple `container` 1.1.0:

- `--network none` gives real isolation. The same container on the default network
  reaches the host at the network gateway, and with `--network none` it cannot.
- A NAT network reaches the whole internet, not only the host. The runtime offers no
  per-container packet filter, so "attach a network and allowlist the model" is not
  isolation.
- A host unix socket bind-mounted into a container is visible but refuses `connect`,
  because virtiofs passes the inode and not the endpoint.

`Network.NAT` exists for one future job that cannot work offline: a step that installs
dependencies before it builds untrusted code. It is never the default, and the UI names
any repository that uses it.

## The model runs on the host

A container on macOS cannot reach Metal or the Apple Neural Engine, so a model inside one
would run on the processor alone. The model server therefore runs on the host, and the
engine calls it.

That makes the engine the only process that holds your code and talks to the network, so
that is where the gate belongs.

## Two gates on egress

- **The engine's HTTP client** refuses any destination that is not on the allowlist
  before a byte leaves, and it does not follow redirects, because a redirect could point
  off the list.
- **A local proxy** covers what the client cannot: a subprocess such as `gh`, or an MCP
  server, started with `HTTPS_PROXY` pointed at it. It logs one line per request with the
  destination, the byte counts, and the verdict.

Matching is exact on host and port. There is no wildcard, because an attacker controls
the label to the left of a domain.

The allowlist holds your model backends and the forges you turned on. Nothing else, until
you add it.

## Hosted models take two switches

`hosted = true` on the backend, and `allow_hosted = true` under `[egress]`. Either one
alone does nothing: the address stays off the allowlist and the gateway refuses the call
with a sentence saying what to change.

## Git never runs your repository's code

`git diff` can run a command through a `textconv` or an external diff driver, but a
driver's command comes from a config file, and `clone` never brings one. A hostile
`.gitattributes` can name a driver and cannot define one.

The rig closes the remaining paths anyway: no system or global config, no hooks, no
external diff, no textconv, and no `ext::` protocol. `--no-optional-locks` keeps git from
writing to your repository. A review never writes to your code.

Anything that does run repository-provided code, a build, a dependency install, or a
linter that loads repository rules, runs in the sandbox.

## Text the rig did not write is data

Three kinds of text reach the model without you having written it: repository hints,
MCP tool output, and forge content. Each is wrapped, labelled, and preceded by a
statement that it is data, that it does not change the rules, and that it does not change
the output format.

## Tokens

A forge token is read from a variable you name, or from `gh auth token` or
`glab auth token`. It is sent in a header, never in a URL, and it is never logged or
written to the config.

An MCP server sees `PATH`, `HOME`, and the variables you list in `pass_env`. It never
sees the engine token or a forge token.

The engine binds the loopback address and requires a bearer token that the application
generates at start. Any local process can reach a loopback port, so every route needs it.

## What the rig writes

- `~/.reviewrig/config.toml`, when you change a setting in the UI.
- `~/.reviewrig/reviewrig.db`, the findings, runs, and code index.
- `~/.reviewrig/models`, downloaded weights.
- A pull request review, and only in `complete` mode.

It never writes to a repository.

## Weaker, and it says so

With no container runtime, analysis falls back to Seatbelt on the host. Seatbelt has no
network and cannot write outside its own scratch, but it shares the host kernel, user,
and file system. The UI shows a banner until a container runtime is installed.

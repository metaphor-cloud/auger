# Models

## The rig brings its own

Open the Models view and press **Set up**. The rig fetches a `llama.cpp` release build
for this machine, fetches weights that fit its memory, writes the config, and starts the
servers. Nothing else to install.

It picks the model by memory: about 80 GB of usable memory gets `gpt-oss-120b`, and a
laptop gets `gpt-oss-20b`. A model the machine cannot hold is shown and cannot be chosen,
because an hour of downloading for weights that will not load is an hour wasted.

Everything it fetches is verified against a sha256 published by the repository. A
download that fails part way carries on from where it stopped, and a file that does not
match its checksum is deleted.

The runtime goes in `~/.reviewrig/runtime`, the weights in `~/.reviewrig/models`.

## Or use a server you already run

A job asks for a job class. The profile picks the backend. No job names a model, so
changing one is a single config line.

| Job class | What it does | Default |
| --- | --- | --- |
| `review` | Reads a diff and reports defects | `gpt-oss-120b` |
| `triage` | Judges static analysis findings | `qwen3-30b-a3b` |
| `embed` | Turns code into vectors for retrieval | `Qwen3-Embedding-0.6B` |
| `rerank` | Orders retrieved code | `Qwen3-Reranker-0.6B` |

## Point a backend at your own server

The rig prefers one: it holds the model you chose, and it may already be warm. Point a
backend at it, and the setup above becomes unnecessary.

```toml
[backend.local-review]
url = "http://127.0.0.1:8080/v1"
model = "whatever-you-loaded"
```

Anything that speaks the OpenAI API works: `llama-server`, `mlx-openai-server`, LM
Studio, Ollama's compatible endpoint.

## What managed means

`managed = true` tells the rig to start that backend when nothing answers at its URL. It
uses the runtime it installed, or one already on the machine, and it says plainly when it
cannot: no runtime, or no weights, each with the step to take next.

```toml
[backend.local-review]
managed = true
model_file = "gpt-oss-120b-mxfp4.gguf"
model_url = "https://huggingface.co/.../gpt-oss-120b-mxfp4.gguf"
```

## How much memory

`gpt-oss-120b` in its native MXFP4 form needs about 63 GB and fits in the unified memory
of a workstation. The Q8 form needs about 120 GB and does not fit in 128 GB, because
macOS holds back a share for the system.

Pick a smaller review model if the machine is smaller. The profile is the only place to
change.

## Hosted models

Off, and they take two switches to turn on:

```toml
[backend.cloud]
url = "https://api.anthropic.com/v1"
model = "claude-sonnet-5"
hosted = true
api_key_env = "ANTHROPIC_API_KEY"

[egress]
allow_hosted = true
```

Either one alone does nothing. With both, your code leaves the machine.

## Batching

`max_concurrent` is the depth the rig keeps a backend at. A continuous batch server stays
full and is never over-committed. Raise it for a small model, keep it low for a large one.

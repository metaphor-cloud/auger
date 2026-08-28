# Models

A job asks for a job class. The profile picks the backend. No job names a model, so
changing one is a single config line.

| Job class | What it does | Default |
| --- | --- | --- |
| `review` | Reads a diff and reports defects | `gpt-oss-120b` |
| `triage` | Judges static analysis findings | `qwen3-30b-a3b` |
| `embed` | Turns code into vectors for retrieval | `Qwen3-Embedding-0.6B` |
| `rerank` | Orders retrieved code | `Qwen3-Reranker-0.6B` |

## Use a server you already run

The rig prefers one: it holds the model you chose, and it may already be warm. Point a
backend at it.

```toml
[backend.local-review]
url = "http://127.0.0.1:8080/v1"
model = "whatever-you-loaded"
```

Anything that speaks the OpenAI API works: `llama-server`, `mlx-openai-server`, LM
Studio, Ollama's compatible endpoint.

## Let the rig run one

Set `managed = true`. The rig starts `llama-server` or `mlx-openai-server`, whichever it
finds, when nothing answers at the URL. It says plainly when it cannot: no server binary,
or no weights.

```toml
[backend.local-review]
managed = true
model_file = "gpt-oss-120b-mxfp4.gguf"
model_url = "https://huggingface.co/.../gpt-oss-120b-mxfp4.gguf"
```

Weights go in `~/.reviewrig/models`. A download reports progress and verifies its
checksum. A failed download leaves no file, because a file that looks complete and is
wrong fails later, inside a review, with a message that says nothing useful.

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

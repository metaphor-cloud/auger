# Models

## The rig brings its own

Open the Models view and press **Set up**. The rig fetches a `llama.cpp` release build
for this machine, fetches weights that fit its memory, writes the config, and starts the
servers. Nothing else to install.

It picks by memory: about 80 GB of usable memory gets `gpt-oss-120b`, and a laptop gets
`gpt-oss-20b`. A model the machine cannot hold is shown and cannot be chosen, because an
hour of downloading for weights that will not load is an hour wasted.

It fetches an embedding model too, and no reranker. See [Retrieval](#retrieval) for the
measurements behind both of those.

Everything it fetches is verified against a sha256 published by the repository. A
download that fails part way carries on from where it stopped, and a file that does not
match its checksum is deleted.

The runtime goes in `~/.auger/runtime`, the weights in `~/.auger/models`.


## Where the weights come from

Auger recommends four models from three families, so a reviewer and a second opinion can
always come from different ones: `gpt-oss-120b` and `gpt-oss-20b`, Meta's
`Muse-Glimmer-30B`, and Google's `gemma-3-12b-qat`.

Anything else is a search away. Models, Find a model takes a few words and lists what
Hugging Face has as a single GGUF file, with what each would cost to run on this
machine. Pick a file and it is fetched and wired to a job class.

### A token

```toml
[models]
token_env = "HF_TOKEN"
```

The config names the variable. It never holds the token, which is the rule the forges
follow, and the value is read at the moment of the request and sent only to Hugging Face
and its delivery hosts.

Two reasons to set one. Some publishers gate their weights behind a licence acceptance,
Google among them, and without a token the rig can only reach somebody's re-upload of
them. A checksum proves the bytes arrived intact; it says nothing about whose weights
they are. And an anonymous download of sixty gigabytes is rate limited.

A gated model is never the recommendation, because a first run that ends in a 401 helps
nobody. It stays in the list to choose on purpose.


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
url = "http://127.0.0.1:1337/v1"
model = "whatever-you-loaded"
```

Anything that speaks the OpenAI API works: `llama-server`, `mlx-openai-server`, LM
Studio, Ollama's compatible endpoint.

What a backend has to answer, by the job class the profile gives it:

| Job class | Endpoint | Also needs |
| -- | -- | -- |
| `review`, `triage`, `verify` | `POST /chat/completions` | `stream: true`, and `response_format` for a JSON schema. Tool calls, if the repository turns tools on. |
| `embed` | `POST /embeddings` | Nothing else. Without it, retrieval falls back to keyword search. |
| `rerank` | `POST /rerank` | Nothing else. Without it, the candidates keep the order retrieval gave them. |

Every answer is streamed, so a bar in the window can report the tokens as they arrive.
A server that cannot stream cannot serve a chat job class here. A server that sends no
`usage` block still works: the token counts are then a lower bound rather than a
measurement.

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

## Retrieval

Retrieval decides what the reviewer sees besides the diff, so the models it uses were
measured rather than chosen.

The test is 25 symbols from this repository with two to six real callers each. The
ground truth is the set of files that reference each symbol, computed from the Python
syntax tree, so it is not built the way any of the retrievers work. Recall counts how
many of those files come back in the top twelve; precision counts how many of the top
five are right.

| configuration | recall@12 | precision@5 | cases with a hit | first correct rank |
| --- | --- | --- | --- | --- |
| keyword only | 0.584 | 0.360 | 22/25 | 2 |
| with `Qwen3-Embedding-0.6B` | 0.613 | 0.416 | 25/25 | 2 |
| with `nomic-embed-code` | **0.686** | **0.448** | 25/25 | **1** |
| with a reranker | 0.373 | 0.144 | 22/25 | 5 |

Three findings, in order of how much they changed.

**Both embedders close the 22-of-25 gap.** Three symbols have callers that exact name
matching finds nothing for. That is what embeddings are for, and it is the strongest
argument for keeping them.

**`nomic-embed-code` is worth its size.** It brings the first correct file from rank 2 to
rank 1, and it is what the rig fetches on a machine with room for it. It costs 4.4 GB
against 0.64 GB and about six times the indexing time, which is paid once and then only
for the files that change.

**A reranker makes it markedly worse**, and it is not fetched. Rank fusion of exact name
search with code embeddings is already a stronger signal than a small cross encoder's
judgement, and the reranker replaces a good ordering with its own. Passing a natural
question rather than the raw diff improved it from 0.337 to 0.373, and it is still far
behind not reranking at all. The catalogue keeps it, because a better reranker may earn
its place later.

To try one, fetch it by hand and name it in a profile:

```toml
[backend.local-rerank]
url = "http://127.0.0.1:1339/v1"
managed = true
model_file = "Qwen.Qwen3-Reranker-0.6B.Q8_0.gguf"
args = ["--reranking"]

[profile.balanced.rerank]
backend = "local-rerank"
```

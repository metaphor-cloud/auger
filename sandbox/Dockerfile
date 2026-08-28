# The analysis image.
#
# It holds every tool that a review step runs, and nothing else. The container has no
# network, so a tool that wants to fetch at run time will fail. Everything must be here.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        git \
        python3 \
        python3-pip \
        python3-venv \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Semgrep and the tree-sitter grammars are used by the static analysis and index steps.
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir \
        "semgrep==1.97.0" \
        "tree-sitter==0.23.2" \
        "tree-sitter-language-pack==0.4.0"
ENV PATH="/opt/venv/bin:${PATH}"

# The repository arrives read only. Scratch is a tmpfs that the run throws away.
RUN mkdir -p /work /scratch && chmod 0777 /scratch
WORKDIR /work

# The rig always sets `--user 65534:65534`. This is the same, for anyone who runs the
# image by hand.
USER 65534:65534

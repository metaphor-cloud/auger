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

# The rules are vendored here. A scan runs with no network, so it cannot fetch them,
# and a scan that reaches a rule registry would also tell that registry what you scan.
# Only the rules survive. Semgrep reads every YAML under the config path as a rule
# file, and the repository carries its own scaffolding: a pre-commit config, workflows,
# templates. One of those is enough to fail the whole scan with a schema error, which
# is what happened the first time this ran.
RUN git clone --depth 1 https://github.com/semgrep/semgrep-rules /opt/semgrep-rules \
    && rm -rf \
        /opt/semgrep-rules/.git \
        /opt/semgrep-rules/.github \
        /opt/semgrep-rules/.circleci \
        /opt/semgrep-rules/stats \
        /opt/semgrep-rules/scripts \
        /opt/semgrep-rules/libsonnet \
    && find /opt/semgrep-rules -maxdepth 1 -type f -delete \
    && find /opt/semgrep-rules -name ".*" -maxdepth 2 -delete \
    && chmod -R a+rX /opt/semgrep-rules

# The subset a scan uses. The full set is 2150 rule files and most of them are
# correctness and style, which is what the reviewer already does better. This job
# exists to find security defects, so it runs the security rules and nothing else.
# The whole set stays above, for a config that asks for it.
# Apex needs the closed source engine, so its rules only ever warn. Leaving them in
# puts a paragraph of "Missing Semgrep extension" on every scan.
RUN mkdir -p /opt/semgrep-security \
    && find /opt/semgrep-rules -path '*/security/*' -name '*.yaml' \
        -not -path '*/apex/*' -exec \
        cp --parents -t /opt/semgrep-security {} + \
    && chmod -R a+rX /opt/semgrep-security \
    # Prove both sets load. A broken ruleset must fail the build, not the first scan.
    && /opt/venv/bin/semgrep --validate --config /opt/semgrep-security \
        --metrics off > /tmp/validate.log 2>&1 \
        || (tail -30 /tmp/validate.log && exit 1)

# Semgrep writes a cache and a settings file. Both go to scratch, which is a tmpfs.
ENV SEMGREP_SETTINGS_FILE=/scratch/semgrep-settings.yml \
    XDG_CACHE_HOME=/scratch/cache \
    HOME=/scratch

# The repository arrives read only. Scratch is a tmpfs that the run throws away.
RUN mkdir -p /work /scratch && chmod 0777 /scratch
WORKDIR /work

# The rig always sets `--user 65534:65534`. This is the same, for anyone who runs the
# image by hand.
USER 65534:65534

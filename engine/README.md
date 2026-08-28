# reviewrig engine

The Python half of [reviewrig](../README.md). It finds git repositories, resolves the
policy for each one, schedules review jobs, runs them in a sandbox, and stores the
findings.

The Tauri application starts this process as a sidecar. It is not meant to be installed
on its own, but it runs standalone for development:

```
uv run reviewrig-engine
```

The first log line carries the port. Every route needs the bearer token from
`REVIEWRIG_TOKEN`.

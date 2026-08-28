# Install

## Requirements

- macOS 13 or later. macOS 26 or later gets Apple `container`, which is the fastest
  sandbox.
- A container runtime: Apple `container`, Podman, or Docker. Without one the rig falls
  back to Seatbelt and says so.
- A local model server, or the rig starts one. See [Models](models.md).

## From a release

Download the `.dmg`, drag `reviewrig` to Applications, and open it. The rig appears in
the menu bar. It has no dock icon, because it runs all day.

On first run it writes `~/.reviewrig/config.toml` pointing at `~/git`, walks it, and
lists what it found.

## From source

```
git clone https://github.com/metaphor-cloud/reviewrig
cd reviewrig
just setup
just build-image      # the analysis image. Needs network.
just dev
```

`just setup` needs `uv`, `cargo`, and `pnpm`.

## First run

1. Open the window from the menu bar.
2. **Repositories** lists what the walk found. Edit `~/.reviewrig/config.toml` to change
   the roots.
3. **Models** shows whether a model server answers. Press Start managed to let the rig
   run one.
4. **Settings** sets what the rig may do, per repository and per organisation. It starts
   in `draft` mode, which writes nothing that anyone else sees.
5. **Findings** fills as reviews finish. **Runs** shows every attempt, including the ones
   that were skipped and why.

## Building a release

```
just package
```

That produces `reviewrig.app`. A `.dmg` that other people can open needs your own Apple
Developer certificate:

```
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_ID="you@example.com"
export APPLE_PASSWORD="app-specific-password"
export APPLE_TEAM_ID="TEAMID"
just release
```

Without a certificate the `.app` still runs on the machine that built it. macOS refuses
an unsigned application that arrived from elsewhere.

## Uninstall

Delete the application, and `~/.reviewrig`. Turn off Start at login first, in the System
view, or the login item stays behind.

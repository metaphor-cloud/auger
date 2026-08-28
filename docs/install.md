# Install

## Requirements

- macOS 13 or later. macOS 26 or later gets Apple `container`, which is the fastest
  sandbox.
- A container runtime: Apple `container`, Podman, or Docker. Without one the rig falls
  back to Seatbelt and says so.
- A local model server, or the rig starts one. See [Models](models.md).

## From a release

Download the `.dmg`, drag `auger` to Applications, and open it. The rig appears in
the menu bar. It has no dock icon, because it runs all day.

On first run it writes `~/.auger/config.toml` pointing at `~/git`, walks it, and
lists what it found.

## From source

```
git clone https://github.com/metaphor-cloud/auger
cd auger
just setup
just build-image      # the analysis image. Needs network.
just dev
```

`just setup` needs `uv`, `cargo`, and `pnpm`.

## First run

On the very first run a short wizard asks the two things it cannot guess: a directory to
watch, and a model to review with. After that:

1. Open the window from the menu bar.
2. Press **Start reviewing** in the sidebar. auger opens stopped, so nothing runs until
   you ask for it.
3. **Work** lists what needs attention, and the strip above it shows every run as it
   happens.
4. **Settings** holds the rest, in the order a first run needs it: where to look,
   models, review, tools, forges, system, advanced. It starts in `draft` mode, which
   writes nothing that anyone else sees.
5. **Runs** shows every attempt, including the ones that were skipped and why.

## Building a release

```
just package
```

That produces `auger.app`. A `.dmg` that other people can open needs your own Apple
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

Delete the application, and `~/.auger`. Turn off Start at login first, in the System
view, or the login item stays behind.

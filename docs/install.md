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
just dev
```

`just setup` needs `uv`, `cargo`, and `pnpm`.

Auger downloads the analysis image on its first run, from
`ghcr.io/metaphor-cloud/auger`. It is a few hundred megabytes, and **System** shows
the progress. A run that starts before the download finished waits for it. If you
were offline at the time, the next run tries again, so nothing needs restarting.

`just build-image` builds it locally instead, which you want only when you are
changing `sandbox/Dockerfile`.

## First run

On the very first run a short wizard asks the two things it cannot guess: a directory to
watch, and a model to review with. After that:

1. Open the window from the menu bar.
2. Press **Start reviewing** in the sidebar. Auger opens stopped, so nothing runs until
   you ask for it.
3. **Work** lists what needs attention, and the strip above it shows every run as it
   happens.
4. **Settings** holds the rest, in the order a first run needs it: where to look,
   models, review, tools, forges, system, advanced. It starts in `draft` mode, which
   writes nothing that anyone else sees.
5. **Runs** shows every attempt, including the ones that were skipped and why.

## If it crashes

A model server holds tens of gigabytes. Auger releases it when you quit, when you press
Unload in the Models view or the tray, and when the application that owns the engine
disappears, because the engine notices and stops on its own.

That leaves one case: the engine itself was killed outright. Its servers are then held by
nothing. Open Auger and press Unload, or run:

```
auger stop
```

It needs no running engine. It stops every model server that came out of `~/.auger`, and
it leaves a `llama-server` you started yourself alone.

## Updates

Auger asks GitHub for a newer release. Open **System** in the window and press **Check
for updates**. It downloads the release, checks it against a key that only this project
holds, and installs it. The new version starts the next time you open Auger.

A build from source has no matching key, so the check there reports a failure. That is
expected.

## Building a release

```
just package
```

That produces `Auger.app`, which runs on the machine that built it. macOS refuses an
unsigned application that arrived from elsewhere, so a `.dmg` that other people can open
needs your own Apple Developer certificate and the updater key:

```
export APPLE_SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_TEAM_ID="TEAMID"
export APPLE_API_KEY="2X9R4HXF34"
export APPLE_API_ISSUER="57246542-96fe-1a63-e053-0824d011072a"
export APPLE_API_KEY_PATH="$HOME/private_keys/AuthKey_2X9R4HXF34.p8"
export TAURI_SIGNING_PRIVATE_KEY="$(cat ~/.tauri/auger-updater.key)"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""
just release
just verify
```

`just release` freezes the engine, signs every Mach-O file inside it, builds the bundle,
notarises it, and writes the archive the updater takes. `just verify` reads the result
back: it fails if one library is unsigned, or if Gatekeeper refuses the application.

The Mac App Store is not a way to ship this. A store application must run in the App
Sandbox, and the App Sandbox permits none of the three things the rig does: it starts a
container runtime, it starts a model server, and it reads the git repositories you name.

## Publishing a release

CI does the whole thing. Push a tag:

```
git tag v0.2.0 && git push origin v0.2.0
```

The workflow checks that the tag agrees with all four version fields, builds, notarises,
writes `latest.json`, and opens a draft release. Read it, then publish it.

Repository secrets hold the keys. Signing and notarisation use different ones, because
they are different acts: the certificate says who built the application, and the notary
ticket says Apple scanned it.

- `APPLE_CERTIFICATE`, the Developer ID certificate as a base64 `.p12`.
- `APPLE_CERTIFICATE_PASSWORD`, the password of that `.p12`.
- `APPLE_SIGNING_IDENTITY` and `APPLE_TEAM_ID`.
- `APPLE_API_KEY`, the ten-character App Store Connect key ID.
- `APPLE_API_ISSUER`, the issuer UUID on the same page.
- `APPLE_API_KEY_BASE64`, the `.p8` file itself, base64 encoded. CI writes it to a file,
  because `notarytool` reads a path and not a variable.
- `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, the updater key.

Export the certificate from Keychain Access, not from the command line. Open the login
keychain, choose **My Certificates**, right-click **Developer ID Application**, and
export it as a `.p12`. The password you give it is `APPLE_CERTIFICATE_PASSWORD`, and
`base64 -i auger-signing.p12` is `APPLE_CERTIFICATE`. Delete the file afterwards.

`security export -t identities` cannot do this. It exports every identity at once, and it
fails on the first private key whose keychain access it cannot satisfy without asking a
person, with `SecKeychainItemExport: The contents of this item cannot be retrieved.`

Make the API key in App Store Connect, under Users and Access, Integrations, App Store
Connect API, with the **Developer** role. Apple lets you download the `.p8` once, so keep
it. A key belongs to the team and not to a person, which is why CI uses one: nobody
leaving revokes it, and you can revoke it on its own.

```
base64 -i AuthKey_2X9R4HXF34.p8 | pbcopy
```

A public repository is safe here. GitHub gives no secret to a workflow that a pull
request from a fork starts, and this workflow runs on a tag, which only a person with
write access can push.

Make the updater key once, and keep it. A lost key means no copy already installed can
take another update.

```
cd app && pnpm tauri signer generate -w ~/.tauri/auger-updater.key
```

The public half goes in `plugins.updater.pubkey` in `app/src-tauri/tauri.conf.json`. The
private half goes in the secret, and nowhere else.

## Uninstall

Delete the application, and `~/.auger`. Turn off Start at login first, in the System
view, or the login item stays behind.

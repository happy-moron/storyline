# Multi-Profile systemd User Service

Run a server under a single systemd user unit, with multiple saved
command-line configurations ("profiles") you can switch between —
while guaranteeing only one instance ever runs at a time and keeping
standard `journalctl` logging.

Each profile runs a single model. Switching between models means stopping the service and restarting it with the profile for the desired new model.

All the different profiles are configured to run the llama-server on http://localhost:11432/
Chat completion endpoint: http://localhost:11432/v1/chat/completions

e.g.
llama-server -m my-model.gguf --port 11432

https://github.com/ggml-org/llama.cpp#web-server

## How it works

- **One static systemd unit** — never edited when you change configs.
- **One `current.conf` symlink** — points at whichever profile file is "active".
- **A runner script** — reads `current.conf`, execs the real server with those args.
- **A CLI script** — repoints the symlink to a chosen profile, then restarts the unit.

Because there's only ever one unit, systemd enforces the single-instance
guarantee for you, and `restart` cleanly stops the old process before
starting the new one.

## Layout

```
~/llamacpp/
├── current.conf -> qwen36-35b-a3b.conf      # symlink, swapped by the CLI
├── qwen36-35b-a3b.conf                       # one profile
└── other-profile.conf                        # another profile

~/.config/systemd/user/llamacpp.service       # static unit, never changes
~/.local/bin/llamacpp-run                     # runner, reads current.conf
~/.local/bin/start_llama                      # CLI to switch profiles / control service
```

### Profile file format

One argument per line. **No trailing whitespace** — a trailing space
turns e.g. `-m` into the literal argument `"-m "`, which the server
will reject as invalid.

```
-m
/mnt/mac/models/gguf/Qwen3.6-35B-A3B-UD-Q4_K_M.gguf
-c
32768
-ngl
99
```

## Components

### systemd unit — `~/.config/systemd/user/llamacpp.service`

```ini
[Unit]
Description=Llama CPP Server

[Service]
ExecStart=%h/.local/bin/llamacpp-run
Restart=on-failure

[Install]
WantedBy=default.target
```

### Runner — `~/.local/bin/llamacpp-run`

Reads `current.conf` line-by-line, trims trailing whitespace/CR, skips
blank lines, and execs the real binary with the resulting argument list.

```bash
#!/bin/bash
set -euo pipefail
CONF="$HOME/llamacpp/current.conf"

ARGS=()
while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] && continue
    ARGS+=("$line")
done < "$CONF"

exec /home/zspdude/git/llama-cpp-turboquant/build/bin/llama-server "${ARGS[@]}"
```

### CLI — `~/.local/bin/start_llama`

Swaps the `current.conf` symlink to the requested profile (atomically,
via `ln -sfn`) and restarts the unit. Also wraps common commands.

```bash
#!/bin/bash
set -euo pipefail
PROFILE_DIR="$HOME/llamacpp"
CURRENT="$HOME/llamacpp/current.conf"

usage() { echo "Usage: start_llama --profile <name> | stop | status | logs"; exit 1; }

case "${1:-}" in
  --profile)
    PROFILE="${2:?profile name required}"
    TARGET="$PROFILE_DIR/$PROFILE.conf"
    [ -f "$TARGET" ] || { echo "No such profile: $PROFILE" >&2; exit 1; }
    ln -sfn "$TARGET" "$CURRENT"
    systemctl --user restart llamacpp.service
    ;;
  stop)   systemctl --user stop llamacpp.service ;;
  status) systemctl --user status llamacpp.service ;;
  logs)   journalctl --user -u llamacpp.service -f ;;
  *) usage ;;
esac
```

Both scripts must be executable (`chmod +x`).

## Usage

```bash
start_llama --profile qwen36-35b-a3b   # switch profile + restart
start_llama stop                       # stop the service
start_llama status                     # systemctl status
start_llama logs                       # tail logs via journalctl
```

## One-time setup

```bash
mkdir -p ~/llamacpp ~/.local/bin
chmod +x ~/.local/bin/llamacpp-run ~/.local/bin/start_llama
systemctl --user daemon-reload
```

To keep the service running after logout: `loginctl enable-linger $USER`.

## Adding a new profile

1. Create `~/llamacpp/<name>.conf` with one arg per line, no trailing whitespace.
2. `start_llama --profile <name>`

## Gotchas

- **Trailing whitespace in profile files** breaks argument parsing —
  strip it with `sed -i 's/[[:space:]]*$//' <file>` if editing by hand.
- The systemd unit file itself should never need to change when adding
  or switching profiles — if it does, something's wrong with the setup.

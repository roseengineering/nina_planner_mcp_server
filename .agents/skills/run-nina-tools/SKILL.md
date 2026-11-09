---
name: run-nina-tools
description: How to invoke the nina-planner CLI tools (nina-archive, nina-purge, nina-pin-id, nina-planner) — they are not on PATH. Resolve the project .venv binary first, then uv run --project, then a PATH copy, else stop. Use before running any of those four commands, or when one of them exits 127 / "command not found".
---

# Running the `nina-*` CLI tools

Four console scripts ship with the `nina-planner` project:

| Tool | What it does |
|---|---|
| `nina-archive` | Copy completed plans + their frames into `DEST` for cloud upload. |
| `nina-purge` | Free the imaging root: quarantine (`--apply`), then delete (`--purge`). |
| `nina-pin-id` | Stamp a plan's current id into `plan_id` before editing it. |
| `nina-planner` | The MCP server itself (already launched by opencode — you rarely run this by hand). |

**They are not installed on PATH.** A bare `nina-archive` exits 127 with `command not found`. That is expected, not a broken install — resolve the command with the branch chain below instead.

## Resolution order — first match wins

Take the **first** branch that applies and use its result for every CLI call in the flow. Do not `uv tool install` the tools or put them on PATH yourself; that is the user's choice to make, not a fix to apply.

1. **Project `.venv` binary — tried first.**
   `.opencode/local/mcps/nina_planner/.venv/bin/<tool>` when that file is executable.
   This is this checkout's own code, it needs no network, and it needs **no `uv` at runtime** — the scripts carry a shebang pointing at that venv's own `python`. This is the branch that covers a host where the user never installed `uv` but has a `.venv` (built here by `make install_uv`, or rsync'd over from another machine).

2. **`uv run --project …` — second.**
   Only if branch 1 misses **and** `command -v uv` succeeds.
   `uv run --project .opencode/local/mcps/nina_planner <tool>` bootstraps an environment of its own, so it works with no `.venv` present — and it does *not* create one. Covers "user has `uv` but no `.venv`".

3. **Bare `<tool>` on PATH — last.**
   Only if branches 1–2 miss **and** `command -v <tool>` succeeds.
   It runs, but it may be a **different build than this checkout** (an older `pip` / `uv tool` install). That matters most for `nina-purge`, which deletes frames and plan JSONs. Say so when this branch is taken — `⚠ using the PATH copy of <tool> — may not be this checkout's version`.

4. **None of the three → stop.**
   With no `.venv`, no `uv`, and nothing on PATH there is nothing left to fall back to: `uv` is what builds the venv, so the venv cannot appear on its own. Report which link is missing and offer the real remedies — `make install_uv` (rebuilds `.venv`), install `uv` (`brew install uv`), or copy a built `.venv` from another host. Never improvise a `pip install -e` of the project, and never proceed to the later steps of the caller's flow: archive/purge cannot run without a working CLI.

```bash
V=.opencode/local/mcps/nina_planner/.venv/bin
resolve() {                                   # $1 = tool name → prints the command, or fails
  if   [ -x "$V/$1" ]; then printf '%s\n' "$V/$1"
  elif command -v uv >/dev/null; then printf 'uv run --project .opencode/local/mcps/nina_planner %s\n' "$1"
  elif command -v "$1" >/dev/null; then echo "⚠ using the PATH copy of $1 — may not be this checkout's version" >&2; printf '%s\n' "$1"
  else return 1; fi
}
RUN_ARCHIVE=$(resolve nina-archive) || { echo "✗ nina-archive unavailable: no .venv, no uv, not on PATH" >&2; exit 1; }
RUN_PURGE=$(resolve nina-purge)     || { echo "✗ nina-purge unavailable: no .venv, no uv, not on PATH" >&2; exit 1; }
RUN_PIN=$(resolve nina-pin-id)      || { echo "✗ nina-pin-id unavailable: no .venv, no uv, not on PATH" >&2; exit 1; }

$RUN_ARCHIVE --dest "$DEST" --dry-run
$RUN_PURGE --dest "$DEST"
```

Resolve **once, before the first command**, not once per step: the venv can disappear under you mid-flow (`make clean` deletes it), and a flow that switches from `.venv` to `uv run` halfway through could purge with a different build than it archived with.

## Environment the CLIs read

`nina-archive` and `nina-purge` take two settings from the **process environment** — they read no config file of their own:

| Var | Meaning | Default when unset |
|---|---|---|
| `NINA_ENDPOINT` | `host:port` of N.I.N.A.'s REST API — probed to read the site profile (the imaging root) | `127.0.0.1:1888` |
| `NINA_DRIVE_MOUNT` | non-Windows mapping for the drive letter in the profile's image save path | `/mnt/<drive>` (usually wrong on macOS) |

Get the live values from `get_nina_settings()` (the MCP server's own env — config file + launch inheritance, so it stays right even when a config file on disk differs from the running server) and prefix **every** invocation with the ones that are set:

```bash
NINA_ENDPOINT=… NINA_DRIVE_MOUNT=… $RUN_ARCHIVE --dest "$DEST" --dry-run
```

If they are `missing`, the defaults apply untouched — correct on a host where N.I.N.A. listens on `127.0.0.1:1888`, wrong for a remote N.I.N.A. Passing these prefixes does not change which resolution branch above is used.

## When the CLI runs but still fails

A working invocation is not a working run. `nina-archive` and `nina-purge` both read the live imaging root from N.I.N.A.'s site profile; when that probe fails they stop with `✗ cannot resolve the imaging root … Is NINA running?`. **That message cannot tell you which probe failed**, so check two things before acting:

1. `get_nina_status()` — is the API answering at `NINA_ENDPOINT`?
2. `get_nina_settings().missing` — is `NINA_ENDPOINT` set at all? Unset means the CLI probed the `127.0.0.1:1888` default regardless of where N.I.N.A. actually lives.

The failures have different fixes: a **wrong/missing endpoint** → pass the set values as env prefixes (section above); a **down API** → start N.I.N.A. on its own host; a **known root path** → skip the probe entirely with `--imaging-root <path>`. None of these is fixed by changing the branch chain.

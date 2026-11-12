# 🔒 Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| `main` (0.1.x) | ✅ |
| < 0.1.0 | ❌ |

The project is pre-1.0; only the latest commit on `main` is supported.

## 📣 Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

- Use **GitHub Private Vulnerability Reporting** on this repository
  (Security → Report a vulnerability), or
- Email: `[YOUR EMAIL HERE]`

Include the impact, steps to reproduce, and any suggested fix. You should get an
acknowledgement within a few days, and we'll coordinate disclosure timing with
you. Reports are credited in the changelog unless you prefer otherwise.

## 🎯 Security Model & Threat Model

This project is an **observatory operator agent**: an MCP tool server that
drives N.I.N.A. (Nighttime Imaging 'N' Astronomy) over its local REST API, plus
an OpenCode `nina-notify` plugin that wakes an agent on observatory events.
Because it moves real hardware — a telescope mount, dome/roof, camera cooler —
**the primary concern is physical safety and equipment protection**, not data
confidentiality.

Assume the following about a default deployment:

- **The trust boundary is your LAN (or localhost).** N.I.N.A.'s REST API is
  plain HTTP with no authentication. This server talks to it at
  `NINA_ENDPOINT` (default `127.0.0.1:1888`) and treats every response as
  authoritative. Anyone who can reach that port can control the rig directly —
  with or without this software in the picture.
- **The agent (opencode / MCP client) is fully trusted.** Every MCP tool is
  effectively a privileged command: `load_sequence`/`start_sequence` slew the
  mount and open the shutter, `start_nina`/`stop_nina` launch or `taskkill`
  NINA, `nina-purge` deletes frames from the imaging root. There is no
  per-tool authorization layer — restricting what the agent may call is the
  job of the agent's own permission configuration.
- **No internet is required for normal operation.** Catalog coordinates are
  pre-verified in `dso_catalog.csv`; only `resolve_target` (SIMBAD) and the
  optional forecast lookup reach the network.

### Assets we care about

| Asset | Why |
|---|---|
| Physical rig (mount, camera, enclosure) | A bad slew or an un-parked mount can cause mechanical damage; the enclosure/safety monitor gates all sky-dependent work. |
| Imaging data + plan files | Months of frames and the `ImageMetaData.csv` attribution that `nina-archive`/`nina-purge` depend on. Purge is destructive and gated on archive manifests, but a wrong `NINA_ARCHIVE_DEST` misconfiguration weakens that gate. |
| Cloud archive destination | `NINA_UPLOAD_CMD` / `NINA_VERIFY_CMD` are executed as shell commands. |
| Host machine | This repo assumes the operator's own machine; a compromised agent has the operator's privileges. |

## 🛡️ Security Considerations for Operators

1. **Bind N.I.N.A.'s API to localhost, or firewall it.** If N.I.N.A. runs on a
   separate machine, reach it over a VPN or an SSH tunnel — not an open port on
   your LAN. Never port-forward the N.I.N.A. API to the internet.
2. **Keep credentials out of config files and environment variables returned to
   the agent.** `get_nina_settings()` returns your `NINA_*` values verbatim —
   treat them as non-secret. Put cloud credentials in the cloud tool's own
   config (e.g. `gcloud`'s application-default auth), never in
   `NINA_UPLOAD_CMD`/`NINA_VERIFY_CMD`.
3. **Review `NINA_UPLOAD_CMD` / `NINA_VERIFY_CMD` before enabling the archive
   flow.** They are arbitrary shell commands run on your behalf; only use
   commands you would run yourself.
4. **Don't commit secrets.** Endpoint hostnames, LAN IPs, bucket names and SSH
   targets appear in the sample configs (`smb_opencode.json`,
   `wsl_opencode.json`, `.opencode/opencode.json`) by design — that's fine for
   a private rig, but keep tokens, passwords and private keys out of them.
5. **Mind the lifecycle tools.** `stop_nina` force-kills `NINA.exe`; avoid
   running it mid-exposure unless safety requires it, and note that
   `start_nina`/`stop_nina` only work on hosts with Windows interop.
6. **`nina-purge` is destructive.** It moves frames out of the imaging root.
   Always review the dry-run output first, and keep `NINA_ARCHIVE_DEST` in the
   MCP config in step with the `--dest` you archive to — a mismatch makes every
   frame read as "not preserved" and holds the purge (the safe failure), but
   don't defeat that gate by pointing both at the wrong place.
7. **Safety semantics are load-bearing.** Sequences always end in a park;
   `is_safe=false` (enclosure closed) gates all sky-dependent acquisition.
   Don't monkey-patch or bypass the safety guardrails in `load_sequence`.

## 🔐 Repository Security

- **CI** (`.github/workflows/ci.yml`) runs lint, type check and tests on
  pushes and pull requests. It uses no repository secrets and does not deploy
  anything, so a CI compromise cannot reach the rig.
- **Dependabot** (`.github/dependabot.yml`) keeps GitHub Actions and Python
  (uv) dependencies updated weekly.
- **Dependency footprint is deliberately small:** `pydantic`, `mcp`, `httpx`,
  `astropy` (+ dev: `pytest`, `ruff`, `mypy`, `pytest-cov`).
- **Code review:** treat changes to `load_sequence`, the safety/park logic,
  `nina-purge`, `client.py`/`system_time.py` (subprocess & `taskkill` paths),
  and the `NINA_UPLOAD_CMD` execution path as security-sensitive.
- **Pre-commit hooks** live in `.githooks/pre-commit`; CI remains the
  authoritative check.

## ✅ Reporting Misconfigurations

If you find a default or documented configuration in this repository that is
insecure by design (an open port, a leaked credential, an unsafe command
example), please report it as a vulnerability — defaults are part of the
security surface.

---
name: archive-lifecycle
description: Archive completed plans, upload the archive to the user's cloud storage, verify the upload, then free the imaging root with nina-purge. Use when the user asks to archive, back up, upload, or clean up finished imaging sets.
---

# Archive → upload → verify → purge

Run these in order. Stop at any failure. Do not skip a step.

## 0. Settings

Read the `environment` block of `.opencode/opencode.json` (the `nina-planner` MCP entry) — that is where these three live; there is no tool for them:

- `NINA_ARCHIVE_DEST` — local archive folder (call it `DEST`).
- `NINA_UPLOAD_CMD` — uploads `DEST` to cloud storage (gcloud, rclone, aws ...).
- `NINA_VERIFY_CMD` — confirms the upload is complete.

If any is unset or blank, stop and tell the user which. **Never continue to purge without `NINA_VERIFY_CMD`.** It is the only check that the cloud copy is whole.

Never add `--delete`-style flags (`--delete-unmatched-destination-objects`, `aws s3 sync --delete`, `rclone sync` semantics) to an upload command; a stale local folder must never remove cloud files. If the configured command does, warn the user.

## 1. Archive

1. `nina-archive --dest "$DEST" --dry-run` and show the result.
2. `nina-archive --dest "$DEST" --checksum`.

Only completed plans are archived; incomplete ones are listed as skipped. Originals are copied, never moved. Pass `--imaging-root` if N.I.N.A. is down.

Degenerate or otherwise-uncredited own-token frames (e.g. a `0.00003s` Smart-Exposure dark) are copied to the folder's `JUNK/` and listed in `manifest.files[]` — a `🗑 … → JUNK/` line means they shipped, which is what lets `nina-purge` read them as preserved. An archive folder built by an older `nina-archive` has no `JUNK/`: re-run with `--force` before purging if such frames are what's holding a directory.

## 2. Upload

Print `NINA_UPLOAD_CMD` and ask the user to confirm it before the first run in a session. Run it with `bash -c`.

## 3. Verify

Run `NINA_VERIFY_CMD`. If it reports anything pending or different, stop: do not purge, and re-run the upload.

## 4. Purge (the imaging root only)

`nina-purge` reads the `manifest.json` files under `DEST` and never modifies `DEST`.

1. `nina-purge --dest "$DEST"` (report only). Show the verdict table.
2. **Ask the user** before `nina-purge --dest "$DEST" --apply`. This moves folders into `.quarantine/`; nothing is deleted. It refuses while a sequence is running.
3. Tell the user to move the quarantined plans' JSON files out of the project directory (they now read as `NEVER_SHOT`).
4. **Ask again** before `nina-purge --dest "$DEST" --purge`. This deletes for real and is irreversible.

## 5. Clean up the local archive

Only after purge, and only after the user confirms: `rm -rf "$DEST"/*/`.

This removes the plan folders but keeps `archive-index.jsonl` (an append-only log), so it keeps growing and the next upload never overwrites the cloud copy with a shorter file. A leftover hidden `.<name>.partial` folder from an interrupted run is not matched; remove it by hand.

Run purge before this step: purge needs the manifests in those folders, and without them it holds every frame as "not preserved".

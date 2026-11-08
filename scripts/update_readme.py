"""Refresh README.md's fenced file excerpts from the files on disk.

Finds ```json <filename> blocks, reads that file's real contents, injects
them into the block, and adds a markdown link above it. This is what keeps
README.md's embedded copies (e.g. the example observation plan) from
drifting away from the files they quote.

Paths are resolved against the PROJECT ROOT -- the parent of this script's
directory -- never against the current working directory, so the script
behaves identically whether it is run by the pre-commit hook, by
`make readme`, or by hand from any subdirectory.

--------------------------------------------------------------------------
WIRING THE PRE-COMMIT HOOK
--------------------------------------------------------------------------

This script does nothing on its own; a hook has to call it. The hook lives
in `.githooks/pre-commit` (committed to the repo, so the whole team gets
the same file). Enabling it is one line:

    git config core.hooksPath .githooks

That is the entire wiring, and yes -- it is just a line in `.git/config`.
Git normally runs hooks from `.git/hooks/`; `core.hooksPath` redirects it
to `.githooks/` instead, where the versioned one lives.

Because `.git/config` is NOT committed, this is per-clone: every teammate
runs it once after cloning (as does any fresh clone on this machine).
Check or undo it with:

    git config core.hooksPath     # -> .githooks
    git config --unset core.hooksPath   # back to the default .git/hooks/

Without that line the hook file sits in the repo doing nothing -- worth
knowing, since a missing hook looks exactly like a working one until
someone commits a stale README.

Run the script directly to refresh without committing:

    python3 scripts/update_readme.py

It is idempotent: running it against an already-fresh README changes
nothing, which is why the hook can safely run it on every commit.
"""

import os
import re
from pathlib import Path

# scripts/update_readme.py -> project root is one level up.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
README_PATH = PROJECT_ROOT / "README.md"

# An optional existing link group, the opening fence + filename (must be on
# the fence line), any old content to replace, then the closing fence. A
# fence with no filename after the language tag does not match and is left
# untouched.
_PATTERN = (
    r"(?P<existing_link>\[[^\]]+\]\([^\)]+\)\s*)?"
    r"(?P<opening>```\w+[ \t]+(?P<filename>[^\n\(\r\)]+)[ \t]*\n)"
    r"(?:[\s\(\S\)]*?)"
    r"(?P<closing>```)"
)


def insert_file_contents_and_links(
    markdown_text: str, base_dir: str | os.PathLike[str] = PROJECT_ROOT
) -> str:
    """Return ``markdown_text`` with every named ```json block refreshed."""
    base = Path(base_dir)

    def replacer(match: re.Match[str]) -> str:
        filename = match.group("filename").strip()
        opening = match.group("opening")
        closing = match.group("closing")

        # Read the file from the project root, but emit a REPO-RELATIVE
        # link (./<filename>): an absolute path would embed this machine's
        # directory layout into a file the whole team shares.
        file_path = base / filename
        if file_path.exists():
            file_contents = file_path.read_text(encoding="utf-8").strip()
        else:
            file_contents = f"<Error: File '{filename}' not found on disk>"

        # Rebuild the block: link above, then the fence with fresh contents.
        return f"[{filename}](./{filename})\n{opening}{file_contents}\n{closing}"

    return re.sub(_PATTERN, replacer, markdown_text)


def main() -> int:
    raw_markdown = README_PATH.read_text(encoding="utf-8")
    updated_markdown = insert_file_contents_and_links(raw_markdown)
    README_PATH.write_text(updated_markdown, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import os
import tempfile
from pathlib import Path

NINA_ENDPOINT = os.environ.get("NINA_ENDPOINT", "127.0.0.1:1888")
NINA_API_URL = f"http://{NINA_ENDPOINT}/v2/api"
SEQUENCE_START_PATH = "/sequence/start"
SEQUENCE_STATE_PATH = "/sequence/state"
SEQUENCE_JSON_PATH = "/sequence/json"
NINA_PLANNER_LOG = os.environ.get("NINA_PLANNER_LOG")
# Directory holding the observatory files: plan JSONs, PLAN.md, PROGRESS.md, report_<YYYY-MM-DD>.md.
# opencode starts local MCP servers with the workspace as their working
# directory, and the launch command uses `uv run --project` (not `uv
# --directory`, which would chdir into the package) — so the process cwd *is*
# the project directory, wherever this package is installed. Like
# SCREENSHOT_DIR, tests patch this directly.
PROJECT_DIR = Path.cwd()
# Screenshots are transient visual checks, so they go to the system temp dir rather
# than the project directory. Patched directly in tests, like PROJECT_DIR.
SCREENSHOT_DIR = Path(tempfile.gettempdir()) / "nina_screenshots"
# NINA's screenshot endpoint captures the window as last painted, so a capture taken
# right after switch-tab can show the previous tab. The repaint settles within a
# couple of seconds on a remote host.
SCREENSHOT_SETTLE_SECONDS = 2.0

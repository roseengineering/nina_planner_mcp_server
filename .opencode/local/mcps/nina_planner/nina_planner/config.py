import os
from pathlib import Path

NINA_ENDPOINT = os.environ.get("NINA_ENDPOINT", "127.0.0.1:1888")
NINA_API_URL = f"http://{NINA_ENDPOINT}/v2/api"
SEQUENCE_START_PATH = "/sequence/start"
SEQUENCE_STATE_PATH = "/sequence/state"
SEQUENCE_JSON_PATH = "/sequence/json"
NINA_PLANNER_LOG = os.environ.get("NINA_PLANNER_LOG")
# Directory holding the observatory files: plan JSONs, PLAN.md, PROGRESS.md,
# report_<YYYY-MM-DD>.md.
# opencode starts local MCP servers with the workspace as their working
# directory, and the launch command uses `uv run --project` (not `uv
# --directory`, which would chdir into the package) — so the process cwd *is*
# the project directory, wherever this package is installed. Tests patch
# this directly.
PROJECT_DIR = Path.cwd()

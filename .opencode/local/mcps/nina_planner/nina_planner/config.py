"""Endpoint, API paths and filesystem roots, all resolved from the environment.

Read once at import time: ``NINA_ENDPOINT`` selects the N.I.N.A. host, and
``PROJECT_DIR`` (the process cwd) anchors plan JSONs and PROGRESS.md.
"""

import os
from pathlib import Path

NINA_ENDPOINT = os.environ.get("NINA_ENDPOINT", "127.0.0.1:1888")
NINA_API_URL = f"http://{NINA_ENDPOINT}/v2/api"
SEQUENCE_START_PATH = "/sequence/start"
SEQUENCE_STATE_PATH = "/sequence/state"
SEQUENCE_JSON_PATH = "/sequence/json"
NINA_PLANNER_LOG = os.environ.get("NINA_PLANNER_LOG")
# Directory holding the observatory files: plan JSONs, PLAN.md, PROGRESS.md,
# report_<YYYY-MM-DD>.md. The process cwd is the project directory (opencode
# starts local MCP servers with the workspace as cwd), and tests patch this.
PROJECT_DIR = Path.cwd()

"""agent:end hook — fire-and-forget capture of the turn's artifacts.

Runs inside the Hermes process, so it uses ONLY the standard library: it spawns
the module's venv python (which has chromadb) as a detached background process
and hands it the turn payload over stdin. It must never raise into the pipeline.

The ${...} placeholders are resolved by the dashboard installer when the bundle
is laid into the install (template: true in the manifest).
"""

import json
import os
import subprocess

VENV_PYTHON = r"${VENV_PYTHON}"
CAPTURE_SCRIPT = r"${INSTALL_DIR}/capture_store.py"
DATA_DIR = r"${HERMES_ROOT}/vector-memory"


async def handle(event_type, context):
    if event_type != "agent:end":
        return
    try:
        payload = json.dumps(
            {
                "user": str((context or {}).get("message") or ""),
                "assistant": str((context or {}).get("response") or ""),
                "session_id": str((context or {}).get("session_id") or ""),
            }
        ).encode("utf-8")

        env = dict(os.environ)
        env["VECTOR_MEMORY_DATA_DIR"] = DATA_DIR

        proc = subprocess.Popen(
            [VENV_PYTHON, CAPTURE_SCRIPT],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=os.path.dirname(CAPTURE_SCRIPT) or None,
        )
        # small payload — write and detach; do NOT wait (off critical path)
        proc.stdin.write(payload)
        proc.stdin.close()
    except Exception:
        # capture is best-effort; never break the turn
        return

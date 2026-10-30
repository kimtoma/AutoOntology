# SPDX-License-Identifier: MIT
"""Keep the supervised application running under launchd, including web restarts."""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from autoontology.config import Settings, unload_env  # noqa: E402
from autoontology.engine.restart import running_restart  # noqa: E402
from autoontology.engine.supervisor import Supervisor  # noqa: E402


def main():
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_args: stopped.set())
    settings = Settings.from_env()
    try:
        while not stopped.is_set():
            if running_restart(settings.run_dir) is None:
                unload_env()
                settings = Settings.from_env()
                try:
                    Supervisor.from_settings(settings, echo=lambda _line: None).up()
                except Exception as exc:  # launchd retries readiness without logging secrets
                    print(f"Application readiness failed: {type(exc).__name__}", flush=True)
            stopped.wait(5)
    finally:
        Supervisor.from_settings(settings).down()


if __name__ == "__main__":
    main()

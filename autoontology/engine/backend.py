# SPDX-License-Identifier: MIT
"""Uvicorn entry point that also installs compatibility in spawned SQL workers."""

from autoontology.engine.upstream_sql import install

# multiprocessing spawn reimports this module as __mp_main__ before loading the
# worker target. Installing at import keeps parent and worker validation identical.
install()

if __name__ == "__main__":
    from uvicorn.main import main

    main()

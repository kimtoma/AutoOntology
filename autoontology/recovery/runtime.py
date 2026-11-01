# SPDX-License-Identifier: MIT
"""Trusted local controller for transferred homes; never execute the archived controller."""

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

from autoontology.recovery import files
from autoontology.recovery import transfer_registry as registry
from autoontology.security import owner_registry as host

APP = Path(__file__).resolve().parents[2]
PG_CTL = Path("/opt/homebrew/opt/postgresql@17/bin/pg_ctl")


def generated(destination):
    old = json.loads((destination / "profile.json").read_text())
    old.update(
        launch_authorized=True,
        controller="trusted-local",
        catalog_ready=False,
        trusted_app=str(APP),
        environment_policy="scrub runtime/provider shell overrides before resolving settings",
    )
    control = (
        "# SPDX-License-Identifier: MIT\nimport sys\nfrom pathlib import Path\nsys.path.insert(0,"
        + repr(str(APP))
        + ")\nfrom autoontology.recovery.runtime import main\nraise SystemExit(main(Path(__file__).parent,sys.argv[1:]))\n"
    ).encode()
    return {
        "profile.json": (
            json.dumps(old, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode(),
        "control.py": control,
    }


def install(destination, journal):
    op = journal["operation_id"]
    for name, data in generated(destination).items():
        current = destination / name
        expected = hashlib.sha256(data).hexdigest()
        if files.stamp(current) == expected:
            continue
        old = (
            journal["staged_pin"]["profile_sha256"]
            if name == "profile.json"
            else journal["old_control_sha256"]
        )
        if files.stamp(current) != old:
            raise registry.error()
        saved = destination / "recovery/activation-originals" / name
        files.copy_file(current, saved, old, op, "copy")
        temporary = current.with_name(".activation-" + op + "-" + name)
        files.write_file(temporary, data, op)
        os.replace(temporary, current)
        files.sync_dir(current.parent)


def _validate(destination):
    marker = host.read(destination / "workspace/autoontology.db")
    if (
        marker is None
        or marker.get("version") != 4
        or marker.get("role") != "destination"
        or marker.get("phase") != "active"
    ):
        raise registry.error()
    registry.guard_binding(marker)
    for name, stamp in marker["runtime_hashes"].items():
        if files.stamp(destination / name) != stamp:
            raise registry.error()
    return marker


def _environment(destination):
    from autoontology import config

    for key in list(os.environ):
        if key.startswith(("AUTOONTOLOGY_", "POSTGRES_", "LLM_", "SEMANTIC_LLM_")) or key in {
            "BACKEND_PORT",
            "INGESTION_SERVICE_URL",
            "APP_URL",
            *config.upstream_model_var_names(),
        }:
            del os.environ[key]
    config._DOTENV_LOADED.clear()
    os.environ["AUTOONTOLOGY_ENV_FILE"] = str(destination / "local.env")
    spec = importlib.util.find_spec("auto_ontology")
    if spec is None or spec.origin is None:
        raise registry.error()
    vendor = Path(spec.origin).parent.parent
    expected = subprocess.check_output(
        ["git", "ls-tree", "HEAD", "vendor/auto-ontology"], cwd=APP, text=True
    ).split()[2]
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=vendor, text=True).strip()
    if expected != actual:
        raise registry.error()
    config.VENDOR_DIR = vendor
    os.environ["PYTHONPATH"] = os.pathsep.join((str(APP), str(vendor)))
    from autoontology.engine import supervisor

    supervisor.VENDOR_DIR = vendor
    return config.Settings.from_env(), supervisor


def main(destination, argv):
    try:
        destination = destination.absolute()
        _validate(destination)
        command = argv[0] if argv else "status"
        if command not in {"start", "stop", "status"}:
            raise registry.error()
        settings, module = _environment(destination)
        if (
            settings.home != destination / "workspace"
            or settings.exports_dir != destination / "exports"
        ):
            raise registry.error()
        if command == "start":
            from autoontology.store.db import Store
            from autoontology.store.identity import Identity

            Identity(Store.for_home(settings.home)).status()
            supervisor = module.Supervisor.from_settings(settings, echo=lambda message: None)
            running = (
                subprocess.run(
                    [str(PG_CTL), "-D", str(destination / "postgres"), "status"],
                    capture_output=True,
                ).returncode
                == 0
            )
            if not running:
                subprocess.run(
                    [
                        str(PG_CTL),
                        "-D",
                        str(destination / "postgres"),
                        "-l",
                        str(destination / "postgres-runtime.log"),
                        "start",
                        "-w",
                    ],
                    check=True,
                    capture_output=True,
                    timeout=60,
                )
            try:
                supervisor.up()
            except BaseException:
                if not running:
                    subprocess.run(
                        [
                            str(PG_CTL),
                            "-D",
                            str(destination / "postgres"),
                            "stop",
                            "-m",
                            "fast",
                            "-w",
                        ],
                        capture_output=True,
                        timeout=60,
                    )
                raise
            from autoontology.recovery.read_review import status as read_status

            print(
                json.dumps(
                    {
                        "started": True,
                        "url": settings.service_url + "/ui/",
                        "catalog_ready": read_status(Store.for_home(settings.home))["query_ready"],
                    }
                )
            )
        else:
            # No identity reads/model expansion are needed to stop this home's
            # children, including when its SQLite metadata has become unreadable.
            service = "autoontology.web.app:app --host 127.0.0.1 --port " + str(settings.port)
            backend = (
                "auto_ontology.server.__main__:create_app --factory --host 127.0.0.1 --port "
                + str(settings.backend_port)
            )
            specs = [
                module.ProcessSpec(
                    "service",
                    [],
                    APP,
                    settings.port,
                    (settings.service_url + "/health/live",),
                    service,
                ),
                module.ProcessSpec(
                    "backend",
                    [],
                    APP,
                    settings.backend_port,
                    (settings.backend_url + "/api/health/live",),
                    backend,
                ),
            ]
            supervisor = module.Supervisor(
                settings.run_dir, settings.logs_dir, specs, echo=lambda message: None
            )
            if command == "stop":
                supervisor.down()
                if (
                    subprocess.run(
                        [str(PG_CTL), "-D", str(destination / "postgres"), "status"],
                        capture_output=True,
                    ).returncode
                    == 0
                ):
                    subprocess.run(
                        [
                            str(PG_CTL),
                            "-D",
                            str(destination / "postgres"),
                            "stop",
                            "-m",
                            "fast",
                            "-w",
                        ],
                        capture_output=True,
                        check=True,
                        timeout=60,
                    )
                print(json.dumps({"stopped": True}))
            else:
                print(
                    json.dumps(
                        {
                            "items": [
                                {"name": s.name, "pid": s.pid, "port": s.port, "healthy": s.healthy}
                                for s in supervisor.status()
                            ]
                        }
                    )
                )
        return 0
    except Exception:
        print(
            json.dumps(
                {
                    "error": "restore_runtime_failed",
                    "detail": "Preserve the transfer journal and runtime. Startup/stop validation did not finish; inspect private local diagnostics.",
                }
            )
        )
        return 1

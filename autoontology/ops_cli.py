# SPDX-License-Identifier: MIT
"""CLI parity for the reviewed, validated publication workflow."""

from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import typer

from autoontology.store.operations import Operations, OperationsError


def commands(settings_for, store_for, fail):
    app = typer.Typer(
        help="Review candidates, configure questions, validate and publish.", no_args_is_help=True
    )

    def run(action, *, edit=None):
        from autoontology.engine.build import WORKSPACE_LEASE
        from autoontology.engine.jobs import JobError, hold_sources
        from autoontology.engine.model_artifacts import ModelArtifactError
        from autoontology.security.policy import AccessError
        from autoontology.store.processing import ProcessingError
        from autoontology.store.provenance import ProvenanceError
        from autoontology.store.publications import PublicationError
        from autoontology.store.restorations import RestoreError

        try:
            settings = settings_for()
            store = store_for(settings)
            with hold_sources(store, [WORKSPACE_LEASE], edit) if edit else nullcontext():
                return action(store, settings)
        except (RestoreError, ModelArtifactError, ProcessingError, AccessError) as exc:
            emit({"error": exc.code, "detail": str(exc)})
            raise typer.Exit(1) from exc
        except ProvenanceError as exc:
            fail(json.dumps({"error": exc.code, "detail": str(exc)}, ensure_ascii=False))
        except PublicationError as exc:
            emit({"error": "publication_invalid", "detail": str(exc)})
            raise typer.Exit(1) from exc
        except (OperationsError, JobError) as exc:
            fail(str(exc))

    def emit(value):
        typer.echo(json.dumps(value, ensure_ascii=False, indent=2))

    @app.command("list")
    def listing():
        from autoontology.provenance import change_originals

        def action(store, settings):
            ops = Operations(store)
            return {
                **ops.state(),
                "items": change_originals(store, ops.changes()),
                "checks": ops.checks(),
            }

        emit(run(action))

    @app.command()
    def decide(
        item: str,
        revision: int = typer.Option(..., "--revision", min=1),
        decision: str = typer.Option(..., "--decision", help="accept | hold | reject"),
        note: str = typer.Option("", "--note"),
    ):
        emit(
            run(
                lambda store, settings: Operations(store).decide(
                    item, decision, candidate=revision, note=note
                ),
                edit="review",
            )
        )

    @app.command("check")
    def check(question: str, expected: str = typer.Option(..., "--expected")):
        emit(
            run(
                lambda store, settings: Operations(store).add_check(question, expected),
                edit="checks",
            )
        )

    @app.command("model-identity")
    def model_identity():
        from autoontology.engine.model_artifacts import embedding_identity

        emit(run(lambda store, settings: embedding_identity(settings)))

    @app.command("processing")
    def processing(source: str):
        from autoontology.store.processing import Processing

        emit(run(lambda store, settings: Processing(store).listing(source)))

    @app.command("processing-record")
    def processing_record(receipt_id: str):
        from autoontology.store.processing import Processing

        emit(run(lambda store, settings: Processing(store).get(receipt_id)))

    @app.command()
    def validate(revision: int = typer.Option(..., "--revision", min=1)):
        from autoontology.engine.operations import validate as validate_candidate

        result = run(
            lambda store, settings: validate_candidate(store, settings, revision=revision),
            edit="validation",
        )
        emit(result)
        if not result["ok"]:
            raise typer.Exit(1)

    @app.command()
    def publish(
        revision: int = typer.Option(..., "--revision", min=1),
        token: str = typer.Option(
            ..., "--token", help="Content-bound validation token, not a login secret."
        ),
    ):
        from autoontology.engine.operations import run_publish

        result = run(
            lambda store, settings: run_publish(store, settings, revision=revision, token=token)
        )
        emit({"run_id": result.run_id, "status": result.status, "revision": result.revision})

    @app.command()
    def documents(
        revision: int = typer.Option(None, "--revision", min=1),
        preview: bool = typer.Option(False, "--preview"),
    ):
        from autoontology.provenance import document_rows, selected_model

        def action(store, settings):
            selected, model = selected_model(store, revision, preview=preview)
            return {
                "revision": selected,
                "preview": preview,
                "items": document_rows(store, model, selected),
            }

        emit(run(action))

    @app.command("source-revisions")
    def source_revisions(name: str):
        from autoontology.store.provenance import SourceArchive

        emit(run(lambda store, settings: {"items": SourceArchive(store).revisions(name)}))

    @app.command("export-view")
    def export_view(
        out: Path = typer.Option(..., "--out", help="New file outside operational exports."),
        format: str = typer.Option("ttl", "--format", help="ttl | jsonld | graphml"),
        revision: int = typer.Option(None, "--revision", min=1),
    ):
        from autoontology.export.view import FORMATS, save
        from autoontology.security.views import knowledge_view
        from autoontology.store.operations import read_snapshot

        def action(store, settings):
            if format not in FORMATS:
                raise OperationsError("Choose ttl, jsonld or graphml.")
            if out.expanduser().resolve().is_relative_to(settings.exports_dir.resolve()):
                raise OperationsError("Write view downloads outside operational exports.")
            selected = read_snapshot(store, revision)
            if selected is None:
                raise OperationsError("No published ontology is available.")
            view = knowledge_view(
                store,
                selected,
                action="exports.read",
                additional_actions=("history.read",) if revision is not None else (),
            )
            return save(store, view, format, out)

        emit(run(action))

    @app.command("publications")
    def publications():
        from autoontology.store.publications import Publications

        emit(run(lambda store, settings: {"items": Publications(store).listing()}))

    @app.command("publication")
    def publication(
        revision: int = typer.Option(..., "--revision", min=1),
        verify: bool = typer.Option(False, "--verify"),
    ):
        from autoontology.store.publications import Publications

        def action(store, settings):
            receipts = Publications(store)
            receipt = receipts.get(revision)
            if receipt is None:
                if not store.get_snapshot(revision):
                    raise OperationsError("unknown revision")
                from autoontology.store.operations import visible_revision

                if not visible_revision(store, revision):
                    raise OperationsError("this candidate has not been published")
                return {"revision": revision, "legacy": True, "manifest": None}
            return {
                **receipt,
                **(
                    {"verification": receipts.verify(revision, settings.exports_dir)}
                    if verify
                    else {}
                ),
            }

        emit(run(action))

    @app.command("restore-prepare")
    def restore_prepare(
        revision: int = typer.Option(..., "--revision", min=1),
        reason: str = typer.Option(..., "--reason"),
    ):
        from autoontology.engine.restorations import prepare_restore

        result = run(
            lambda store, settings: prepare_restore(
                store, settings, revision=revision, reason=reason
            )
        )
        emit(result)
        if result["status"] != "ready":
            raise typer.Exit(1)

    @app.command("restores")
    def restores():
        from autoontology.store.restorations import Restorations

        emit(run(lambda store, settings: {"items": Restorations(store).listing()}))

    @app.command("restoration")
    def restoration(proposal: str):
        from autoontology.store.restorations import Restorations

        emit(run(lambda store, settings: Restorations(store).get(proposal)))

    @app.command("restore")
    def restore(
        proposal: str = typer.Option(..., "--proposal"),
        confirmation: str = typer.Option(..., "--confirm"),
    ):
        from autoontology.engine.restorations import run_restore

        result = run(
            lambda store, settings: run_restore(
                store, settings, proposal=proposal, confirmation=confirmation
            )
        )
        emit(vars(result))

    @app.command("preserve-original")
    def preserve_original(
        document: str,
        revision: int = typer.Option(None, "--revision", min=1),
        preview: bool = typer.Option(False, "--preview"),
    ):
        from autoontology.provenance import preserve_original as preserve
        from autoontology.provenance import selected_model

        def action(store, settings):
            selected, model = selected_model(store, revision, preview=preview)
            return preserve(store, model, document, selected)

        emit(run(action, edit="archive"))

    @app.command()
    def original(
        document: str,
        out: Path = typer.Option(
            ..., "--out", help="New local file; existing files are never overwritten."
        ),
        revision: int = typer.Option(None, "--revision", min=1),
        preview: bool = typer.Option(False, "--preview"),
    ):
        from autoontology.provenance import save_original, selected_model

        def action(store, settings):
            selected, model = selected_model(store, revision, preview=preview)
            return {
                "revision": selected,
                "preview": preview,
                **save_original(store, model, document, out),
            }

        emit(run(action))

    from autoontology.query_cli import install

    install(app, settings_for)
    from autoontology.cases_cli import install as install_cases

    install_cases(app, settings_for, store_for)
    from autoontology.quality_cli import install as install_quality

    install_quality(app, settings_for, store_for)
    from autoontology.ax_cli import install as install_ax

    install_ax(app, settings_for, store_for)
    from autoontology.host_files_cli import install as install_host

    install_host(app, settings_for, store_for)
    return app

# SPDX-License-Identifier: MIT
"""Serialize an already-authorized view without loading unfiltered export bundles."""

import hashlib
import os
import tempfile
from io import BytesIO
from pathlib import Path

from autoontology.export import graphml, rdf

FORMATS = {
    "ttl": "text/turtle",
    "jsonld": "application/ld+json",
    "graphml": "application/graphml+xml",
}


def render(model, format):
    if format not in FORMATS:
        raise ValueError("Choose ttl, jsonld or graphml.")
    if format == "graphml":
        stream = BytesIO()
        graphml.to_xml(graphml.to_graph(model)).write(
            stream, encoding="utf-8", xml_declaration=True
        )
        return stream.getvalue()
    graph = rdf.to_graph(model)
    return graph.serialize(format="turtle" if format == "ttl" else "json-ld", encoding="utf-8")


def save(store, view, format, destination):
    """Write new private bytes atomically after a final live-authority check."""
    from autoontology.security.views import verify_view_authority
    from autoontology.store.provenance import ProvenanceError

    destination = Path(destination).expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ProvenanceError("Output already exists; choose a new file.", code="output_exists")
    payload = render(view.model, format)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".knowledge-view-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        verify_view_authority(store, view.model, "exports.read")
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise ProvenanceError(
                "Output already exists; choose a new file.", code="output_exists"
            ) from None
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {
        "revision": view.snapshot.revision,
        "restricted": view.restricted,
        "format": format,
        "file": str(destination),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }

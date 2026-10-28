# SPDX-License-Identifier: MIT
"""Element index: the embedded :class:`~autoontology.playground.elements.Element` texts of one
source's merged ontology, persisted in ``playground_index`` and refreshed when the ontology
revision (or the embedding model) changes; an element whose text and model are unchanged
keeps its stored vector."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from autoontology.playground.elements import Element
from autoontology.playground.store import IndexRow, PlaygroundStore

logger = logging.getLogger("autoontology.playground")

#: (texts, input_type "passage"|"query") -> one vector (or None) per text
Embedder = Callable[[list[str], str], list[list[float] | None]]
EMBED_BATCH = 64
DEFAULT_TOP_K = 12
CLASS_TYPES = frozenset({"term", "concept", "rule", "process", "individual"})


@dataclass
class Hit:
    element: Element
    score: float | None  # cosine; None = added as a neighbour
    via: str = "retrieval"  # retrieval | neighbour


class ElementIndex:
    def __init__(
        self,
        elements: list[Element],
        vectors: list[list[float] | None],
        *,
        model: str,
        revision: int,
        reused: int = 0,
        embedded: int = 0,
    ) -> None:
        self.elements = elements
        self.by_id = {e.id: e for e in elements}
        self.model = model
        self.revision = revision
        self.reused = reused
        self.embedded = embedded
        keep = [i for i, v in enumerate(vectors) if v]
        self._rows = keep
        if keep:
            matrix = np.asarray([vectors[i] for i in keep], dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._matrix = matrix / norms
        else:
            self._matrix = np.zeros((0, 1), dtype=np.float32)

    def __len__(self) -> int:
        return len(self.elements)

    def search(self, vector: list[float], top_k: int = DEFAULT_TOP_K) -> list[Hit]:
        if not self._rows or not vector:
            return []
        q = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(q)) or 1.0
        scores = self._matrix @ (q / norm)
        order = np.argsort(-scores, kind="stable")[:top_k]
        return [Hit(self.elements[self._rows[i]], round(float(scores[i]), 4)) for i in order]

    def retrieve(
        self, vector: list[float], top_k: int = DEFAULT_TOP_K, max_neighbours: int | None = None
    ) -> list[Hit]:
        """Top-*top_k* by cosine, then the 1-hop neighbours of every retrieved class-like
        element (relations touching it and the nodes at their other ends), in rank order,
        at most *max_neighbours* (default *top_k*) of them."""
        hits = self.search(vector, top_k)
        cap = top_k if max_neighbours is None else max_neighbours
        seen = {h.element.id for h in hits}
        extra: list[Hit] = []
        for h in hits:
            if h.element.type not in CLASS_TYPES:
                continue
            for nid in h.element.neighbours:
                if len(extra) >= cap:
                    break
                if nid in seen or nid not in self.by_id:
                    continue
                seen.add(nid)
                extra.append(Hit(self.by_id[nid], None, "neighbour"))
        return hits + extra


def build_index(
    store: PlaygroundStore,
    scope: str,
    elements: list[Element],
    *,
    revision: int,
    model: str,
    embed: Embedder,
) -> ElementIndex:
    """Refresh the stored index of *scope* to *elements* and return it in memory.

    Vectors are reused for rows with the same element id, text sha and model; only the
    rest are embedded. Raises whatever *embed* raises (nothing is written then)."""
    stored = store.index_rows(scope)
    vectors: list[list[float] | None] = []
    todo: list[int] = []
    for i, e in enumerate(elements):
        row = stored.get(e.id)
        if row and row.text_sha == e.text_sha and row.model == model and row.embedding:
            vectors.append(row.embedding)
        else:
            vectors.append(None)
            todo.append(i)
    for start in range(0, len(todo), EMBED_BATCH):
        batch = todo[start : start + EMBED_BATCH]
        got = embed([elements[i].text for i in batch], "passage")
        if len(got) != len(batch):
            raise RuntimeError(f"embedder returned {len(got)} vectors for {len(batch)} texts")
        for i, v in zip(batch, got, strict=True):
            vectors[i] = list(v) if v else None
    store.replace_index(
        scope,
        [
            IndexRow(e.id, e.type, e.text_sha, v, model)
            for e, v in zip(elements, vectors, strict=True)
        ],
        revision=revision,
        model=model,
    )
    logger.info(
        "playground index for %s: %d element(s), %d embedded, %d reused (rev %d)",
        scope,
        len(elements),
        len(todo),
        len(elements) - len(todo),
        revision,
    )
    return ElementIndex(
        elements,
        vectors,
        model=model,
        revision=revision,
        reused=len(elements) - len(todo),
        embedded=len(todo),
    )

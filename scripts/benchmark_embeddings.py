# SPDX-License-Identifier: MIT
"""Read-only, reproducible Ollama retrieval evaluation using frozen relevance IDs.

Dataset JSON: ``corpora`` maps a name to [{id, text, label}], and ``questions``
contains {id, question, gold: {corpus_name: [relevant_id, ...]}}. It never writes
the application workspace. Run with the project's Python environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx
import numpy as np

from autoontology.engine.embeddings import prepare_inputs


def normalized(vectors):
    matrix = np.asarray(vectors, dtype=np.float32)
    return matrix / np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--endpoint", default="http://localhost:11434")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument(
        "--models", nargs="+", default=["bge-m3", "qwen3-embedding:0.6b", "qwen3-embedding:4b"]
    )
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text())
    args.output.mkdir(parents=True, exist_ok=True)
    report = {
        "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "revision": dataset.get("revision"),
        "repetitions": args.repetitions,
        "method": dataset.get("method"),
        "models": {},
    }
    with httpx.Client(base_url=args.endpoint, timeout=600) as client:
        tags = client.get("/api/tags").json()["models"]
        report["ollama_version"] = client.get("/api/version").json()["version"]

        def embed(model, texts, kind):
            response = client.post(
                "/api/embed",
                json={
                    "model": model,
                    "input": prepare_inputs(texts, model, kind),
                    "truncate": False,
                    "keep_alive": "10m",
                },
            )
            response.raise_for_status()
            payload = response.json()
            vectors = payload["embeddings"]
            assert len(vectors) == len(texts) and all(vectors)
            return vectors

        for model in args.models:
            print(f"Evaluating {model}", flush=True)
            start = time.perf_counter()
            warm = embed(model, ["임베딩 모델 준비 문장"], "passage")
            result = {
                "dimension": len(warm[0]),
                "initial_request_seconds": round(time.perf_counter() - start, 3),
                "download_bytes": next(
                    (t["size"] for t in tags if t["name"] in [model, model + ":latest"]), None
                ),
                "corpora": {},
            }
            qtexts = [q["question"] for q in dataset["questions"]]
            query_vectors = []
            latencies = []
            for repetition in range(args.repetitions):
                run = []
                for question in qtexts:
                    start = time.perf_counter()
                    vector = embed(model, [question], "query")[0]
                    latencies.append((time.perf_counter() - start) * 1000)
                    run.append(vector)
                if repetition == 0:
                    query_vectors = run
            result["query_ms_p50"] = round(float(np.median(latencies)), 2)
            result["query_ms_p95"] = round(float(np.percentile(latencies, 95)), 2)
            result["query_samples"] = len(latencies)
            qmatrix = normalized(query_vectors)
            for name, passages in dataset["corpora"].items():
                vectors = []
                start = time.perf_counter()
                for offset in range(0, len(passages), 16):
                    vectors.extend(
                        embed(model, [p["text"] for p in passages[offset : offset + 16]], "passage")
                    )
                    if offset % 160 == 0:
                        print(f"  {name}: {len(vectors)}/{len(passages)}", flush=True)
                seconds = time.perf_counter() - start
                np.savez_compressed(
                    args.output / f"{model.replace(':', '-')}-{name}.npz",
                    vectors=np.array(vectors, dtype=np.float32),
                )
                scores = qmatrix @ normalized(vectors).T
                details = []
                for question, row in zip(dataset["questions"], scores, strict=True):
                    order = np.argsort(-row, kind="stable")
                    gold = set(question["gold"].get(name, []))
                    rank = next(
                        (n for n, i in enumerate(order, 1) if passages[i]["id"] in gold), None
                    )
                    details.append(
                        {
                            "id": question["id"],
                            "gold_count": len(gold),
                            "rank": rank,
                            "top5": [
                                {
                                    "id": passages[i]["id"],
                                    "label": passages[i]["label"],
                                    "score": round(float(row[i]), 5),
                                }
                                for i in order[:5]
                            ],
                        }
                    )
                eligible = [d for d in details if d["gold_count"]]
                result["corpora"][name] = {
                    "passages": len(passages),
                    "eligible_questions": len(eligible),
                    "total_questions": len(details),
                    "embedding_seconds": round(seconds, 3),
                    "hit_at_1": sum(d["rank"] == 1 for d in eligible) / len(eligible),
                    "hit_at_5": sum(d["rank"] <= 5 for d in eligible) / len(eligible),
                    "hit_at_12": sum(d["rank"] <= 12 for d in eligible) / len(eligible),
                    "mrr_at_12": sum(1 / d["rank"] if d["rank"] <= 12 else 0 for d in eligible)
                    / len(eligible),
                    "details": details,
                }
            report["models"][model] = result
            (args.output / "results.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
            )
            print(json.dumps({k: v for k, v in result.items() if k != "corpora"}), flush=True)
            # Only unload the evaluated model. Other local applications keep theirs.
            client.post(
                "/api/embed", json={"model": model, "input": [], "keep_alive": 0}
            ).raise_for_status()


if __name__ == "__main__":
    main()

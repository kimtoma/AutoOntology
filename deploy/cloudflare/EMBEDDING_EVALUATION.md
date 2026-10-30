# Korean embedding evaluation on the deployed workspace

Date: 2026-10-30. Scope: the isolated `public-demo` Cloudflare workspace only.
The primary local workspace, source files, ontology snapshots, existing exports,
source databases, and prior conversations are preserved.

## Corpus and method

The existing five Korean documents (HWP, PDF, DOCX and Markdown) provide 28 stored
chunks. The actual question-answering index contains 804 ontology elements,
including definitions, rules, relations and quoted evidence. Forty Korean questions
cover contract approval, renewal, billing, overdue payments and project controls.
Questions and supporting evidence IDs were frozen before model inference. Every
model receives the same corpus and questions; Qwen queries use its official retrieval
instruction and documents remain unprefixed. No LLM judges the retrieval results.

A relevant passage must contain all manually specified supporting phrases after
whitespace normalization. Multiple passages can be relevant. These conservative
labels can miss equivalent paraphrases. One question about first-day telephone
collection has evidence in the source chunks but none in the extracted ontology;
ontology retrieval percentages therefore use the same 39 eligible questions for
all models. This is a small evaluation of this workspace, not a general Korean
benchmark. Scores measure retrieval, not overall answer accuracy.

Dataset SHA-256:
`9800b66c4466d74e0b5f11a94821810ee492327b933b92ac69cd339d5d980f3a`.

Ollama version: `0.40.11`. Query latency measures 120 sequential, warmed requests
per model (40 questions repeated three times), excluding chat generation, network
access through Cloudflare, and initial model loading. Download size is not RAM use.

## Results

| Model | Download | Dimensions | Raw chunk Hit@1 | Ontology Hit@1 | Ontology Hit@5 | Ontology Hit@12 | Ontology MRR@12 | Query p50 / p95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| bge-m3 | 1.16 GB | 1024 | 77.5% (31/40) | 74.4% (29/39) | 94.9% (37/39) | 97.4% (38/39) | 0.824 | 23.74 / 28.02 ms |
| Qwen3 Embedding 0.6B | 639 MB | 1024 | 85.0% (34/40) | 71.8% (28/39) | 84.6% (33/39) | 92.3% (36/39) | 0.777 | 32.16 / 34.94 ms |
| Qwen3 Embedding 4B | 2.50 GB | 2560 | 90.0% (36/40) | 64.1% (25/39) | 87.2% (34/39) | 100% (39/39) | 0.752 | 165.96 / 170.68 ms |

All three reach raw-chunk Hit@5 of 40/40. Embedding all 804 ontology elements took
20.25 s, 23.08 s and 131.48 s respectively in the benchmark.

## Selection

Use **`qwen3-embedding:4b`** for this workspace. The application sends its first
**12** retrieval hits, plus graph neighbors, to Codex. Coverage within that actual
context budget matters more than top-one or top-five rank. Qwen 4B retrieves the
support for all 39 eligible questions and also has the best raw-chunk top-one score.
Its additional approximately 142 ms of warm embedding latency is small compared
with the measured 6–8 s Codex answers in the diagnostic cases.

To check the practical consequence, two diagnostic questions with different model
rankings were run through the real application prompt builder, the same top-12 plus
neighbor retrieval, and the existing Codex router: six completed model calls total.
All three answer the partial-payment question. For the 30-day overdue notice
question, bge-m3 and Qwen 0.6B report insufficient evidence; Qwen 4B answers that a
written collection notice is sent and cites the original evidence. These two cases
are diagnostic examples selected after retrieval evaluation, not an unbiased
answer-accuracy benchmark. bge-m3 remains better for early ranking, latency and
resource use; it was not selected because answer support is the priority here.

The source-to-ontology extraction gap remains: changing an embedding model cannot
recover facts absent from its retrieval corpus. A larger or different document set
should be evaluated again rather than assuming this choice is universally best.

## Application and rollback

Before cutover, SQLite was backed up with its online backup API and both dedicated
Postgres databases with `pg_dump -Fc`; dump tables of contents and SQLite integrity
were verified. Backups, frozen questions/corpora, full per-question ranks, vector
matrices, completed diagnostic answers and external browser evidence remain under
the ignored `data/cloudflare-demo/embedding-evaluation/` directory. They must not be
published. `backup-path.txt` identifies the most recent pre-cutover backup directory.

The cutover re-embeds 804 playground elements, 44 data-layer rows, 40 semantic-layer
rows and three current alignment terms: **891 live vectors**. Postgres vectors move
from `halfvec(1024)` to `halfvec(2560)` using a staged column and transactional swap;
HNSW cosine indexes are recreated. IDs, content and metadata are retained. SQLite
index model identifiers and the environment selection change together during the
application maintenance interval. Old alignment cache entries remain keyed by their
old model and are not reused by the new model. Vendor files remain unchanged.

Rollback requires stopping the dedicated application, restoring the pre-cutover
SQLite database and the `autoontology` catalog dump together, restoring the saved
`demo.env`, then restarting the application. Restore the source database only if
needed: the embedding migration does not alter it. Never restore across the primary
Postgres instance on port 5432. A model-name-only rollback would mix vector spaces
and is not sufficient.

The reusable read-only evaluator can be rerun with the frozen dataset:

```bash
.venv/bin/python scripts/benchmark_embeddings.py \
  data/cloudflare-demo/embedding-evaluation/dataset.json \
  --output data/cloudflare-demo/embedding-evaluation/recheck
```

Qwen input format reference:
https://huggingface.co/Qwen/Qwen3-Embedding-0.6B#usage.

## Completed verification

After cutover, content hashes match for 22 SQLite business tables and all 37
Postgres content tables; 637 protected source/export/configuration files match.
All nine prior conversations and 18 prior messages remain identical; the browser
checks add four conversations and eight messages. SQLite integrity passes and all
live vectors have 2560 dimensions. The running app reports Qwen 4B embeddings,
Codex chat, and no pending restart. All four dedicated LaunchAgents are running.

Headless Chromium verified the actual external hostname, not a local substitute:

- Wrong password rejected; current password accepted; fresh 804-element index.
- Graph renders 120 nodes and 190 links; original quoted evidence opens.
- Four completed Codex answers with citations: CEO approval at KRW 300 million,
  partial payment, written notice after 30 days overdue, and the HWP security pledge.
- Clickable citations open source evidence details.
- SQL executes `COUNT(*)` against the source and returns 40 customers; CSV downloads.
- The 234,196-byte Turtle export downloads and matches its local file exactly.
- Cross-origin writes and direct-origin access return 403; after logout settings
  return 401. No browser page errors were observed.

Relevant embedding, playground, settings, jobs, alignment, supervisor, configuration
and upstream-compatibility regression tests passed. The existing PID-probe test
requires process access and passed when rerun outside the restricted sandbox.
Ruff lint/format and `git diff --check` pass. Deployment secret values were not
found in changed source files or runtime logs.

# ADR-0002: Text embedding model for opsman

- Status: Accepted
- Date: 2026-09-30
- Part of: [ADR-0001](0001-opsman-in-cluster-agent.md), sub-project 0

## Context

opsman stores text in Qdrant and finds it again by meaning: manifests, events,
log tails, READMEs, summaries of past runs, its own skills. That needs an
embedding model. The constraints:

- **English text.** A corpus with Ukrainian (or other languages) reopens this
  ADR.
- **CPU only.** abox is KinD on a Codespace (8 CPU, 31 GB), no GPU.
- **Served by llama.cpp,** so the model must exist as GGUF. llama.cpp is what
  abox already runs on both embedding routes, and it serves an
  OpenAI-compatible `/v1/embeddings`.
- **Small enough to run next to opsman** as a sidecar ([ADR-0003](0003-embeddings-in-cluster.md)).
- **Long inputs.** One Deployment manifest or event list is often over 512
  tokens, so a short context window means heavy chunking and lost context.
- **Permissive license.**

## Options

| | nomic-embed-text-v1.5 | bge-small-en-v1.5 | embeddinggemma-300m | Qwen3-Embedding-0.6B |
|---|---|---|---|---|
| Parameters | 137M | 33.4M | 300M | 0.6B |
| Dimensions | 768, truncatable to 512/256/128/64 | 384 | 768, truncatable to 512/256/128 | up to 1024, truncatable to 32 |
| Context | 2048 by default in llama.cpp, 8192 with YaRN | 512 | 2048 | 32K |
| License | Apache 2.0 | MIT | Gemma terms, gated on Hugging Face | Apache 2.0 |
| MTEB English | 62.28 (v1) | 62.17 (v1) | 69.67 (v2) | 70.70 (v2) |
| Q8_0 GGUF | 140 MiB | 35 MiB | 318 MiB | 610 MiB |
| Required prompt | `search_query: ` / `search_document: ` | query instruction, optional | `task: search result \| query: ` | `Instruct: ...\nQuery:` |

MTEB v1 and v2 are different benchmarks; compare scores only within the same
version. Figures are from the model cards and the GGUF repos on Hugging Face.

- **nomic-embed-text-v1.5.** The longest useful window in the small class, and
  dimensions can be cut later to shrink Qdrant. Already baked into
  `ghcr.io/den-vasyliev/abox/nomic-embed`, served on both abox routes, and
  behind the `abox-nomic` collection. Every text needs its task prefix; a
  missing prefix degrades retrieval without any error.
- **bge-small-en-v1.5** (and all-MiniLM-L6-v2, 22.7M, 384 dims, which
  `qdrant-mcp-official` uses through fastembed). Cheapest on CPU, but 512 and
  256 token windows. MiniLM truncates anything longer.
- **embeddinggemma-300m.** Stronger scores, but the Gemma terms are not an OSI
  license and the weights are gated. 2.3x nomic's Q8_0 size.
- **Qwen3-Embedding-0.6B.** Strongest here and 32K context, but 4.4x nomic's
  Q8_0 size, paid again in every opsman pod.

## Decision

Use **nomic-embed-text-v1.5, Q8_0 GGUF**, served by llama.cpp with
`--embeddings`.

- **Weights:** `nomic-embed-text-v1.5.Q8_0.gguf` from
  `nomic-ai/nomic-embed-text-v1.5-GGUF`, 146,146,432 bytes, sha256
  `3e24342164b3d94991ba9692fdc0dd08e3fd7362e0aacc396a9a5c54a544c3b7`.
  In the cluster it comes from `ghcr.io/den-vasyliev/abox/nomic-embed@sha256:74be142b7496cdf2ca0dbf9bd64c98648e1a64147795b61e569168cc4dd439fd`
  (tag `v1.18.1-4ccc0ff`, amd64 only).
- **Window:** 2048 tokens per input. opsman chunks longer text. YaRN (8192)
  stays off: it changes the positional encoding of every input, so turning it
  on is a new vector space, not a setting.
- **Dimensions:** store all 768. Truncating needs nomic's layer-norm step, not
  a plain slice, and would be a new collection.
- **Prefixes:** opsman's embedding client adds `search_document: ` when storing
  and `search_query: ` when searching. Callers never add them.
- **One collection, one model.** A collection holds vectors from exactly one
  GGUF, named after it (for example `nomic-v1.5-q8_0-768`). Everything that
  writes to or reads from a collection pins the same image digest. Changing
  the model or the quant means re-embedding into a new collection.

## Evidence

Measured on 2026-09-30 against the Codespace cluster, the same query on both
routes (`search_query: which pods are not ready?`):

| | standalone `llama-cpp-embeddings:8090` | llm-d `llm-d-embedding:8000` |
|---|---|---|
| llama.cpp build | b9438 | b10920 |
| `n_params` | 136,727,040 | 136,727,040 |
| weights (`meta.size`) | 145,389,792 bytes | 145,389,792 bytes |
| `ftype` | not reported by this build | Q8_0 |
| `n_ctx_train` | 2048 | 2048 |
| dims | 768 | 768 |
| first three values | -0.04349, 0.02644, -0.17724 | -0.04349, 0.02644, -0.17724 |
| memory | 293.5 MiB RSS | 16.3 MiB RSS, 144.4 MiB working set (mmap'd from an image volume) |

Both routes serve the same Q8_0 file and returned the same vector. That is one
probe, not a proof; [the cluster ToDo](../todo/embeddings-cluster.md) checks
more texts before any consumer switches routes.

## Consequences

- No new image or service; opsman shares a vector space with `retrieval-agent`
  as long as both pin the same GGUF.
- Prefix discipline lives in one place, the client, or retrieval quietly gets
  worse.
- A 2048 window means chunking for long objects; the chunk size is opsman's
  decision, not the model's.
- Follow-ups, not done here. These describe the published image wrongly:
  - `images/nomic-embed/Dockerfile` defaults to the f16 file and builds
    multi-arch, but the published `v1.18.1-4ccc0ff` is Q8_0 and amd64 only
  - `mcp/qdrant-mcp/README.md` lists the standalone route as f16
  - `releases/mcp-servers.yaml` calls the standalone route f16 and the two
    routes "two spaces"
  - `releases/llama-cpp-embeddings.yaml` puts resident memory at about 75 MiB;
    it was 293.5 MiB today

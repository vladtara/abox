# Changelog

Notable changes to abox. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), plus a `Found`
section for measured problems that are not fixed yet. Releases of `releases/`
are the `v*` tags that CI publishes as OCI artifacts.

## [Unreleased]

### Added

- [ADR-0004](docs/adr/0004-opsman-kernel.md): the opsman kernel (sub-project
  1), a controller-runtime operator. It proves itself on one loop: detect,
  diagnose, propose a fix PR, then release after a human merge and verify.
  - Watches feed detectors written as plain code; only a new finding starts a
    Run.
  - A `Run` CRD holds the phase and an append-only journal; artifacts go in
    ConfigMaps owned by the Run.
  - The roles (diagnoser, fixer, oracle) are kagent Declarative Agents over
    A2A. The kernel has no LLM client.
  - The fix gate enforces scope, deny rules, size, render and a secret scan.
  - The tag gate tags the merge commit only if nothing newer was released.
  - RBAC is read-only outside its own namespace. The rollout goes observe,
    then propose, then full.
- [ADR-0001](docs/adr/0001-opsman-in-cluster-agent.md): opsman, a Go agent
  that runs only in the cluster and manages it in a loop. It is registered
  as a kagent `BYO` Agent and keeps the stance of
  [glapsfun/opsman](https://github.com/glapsfun/opsman): the model proposes,
  a deterministic kernel decides. It changes the cluster through GitOps, with
  R0 to R4 risk gates. The work is split into sub-projects 0 to 5.
- [ADR-0002](docs/adr/0002-embedding-model.md): opsman embeds text with
  **nomic-embed-text-v1.5, Q8_0 GGUF**, on llama.cpp.
  - Compared with bge-small-en-v1.5, all-MiniLM-L6-v2, embeddinggemma-300m and
    Qwen3-Embedding-0.6B.
  - Pinned by sha256 and by image digest.
  - Working window of 2048 tokens.
  - Task prefixes are added by the client.
  - A collection holds vectors from exactly one model.
- [ADR-0003](docs/adr/0003-embeddings-in-cluster.md): opsman gets its own
  embedder as a **sidecar**.
  - It is built like llm-d's decode container, on llama.cpp b10920 with the
    GGUF from an image volume, and binds `127.0.0.1:8090`.
  - **llm-d** stays the shared route for other consumers and for scaling.
  - The standalone Deployment is left to `qdrant-mcp`.
- [ToDo: local run](docs/todo/embeddings-local.md): a checklist for the agent
  to run the model with Docker and llama.cpp and call `/v1/embeddings`. It
  covers the checksum, model metadata, dims, a prefix retrieval check and an
  Ollama fallback.
- [ToDo: cluster](docs/todo/embeddings-cluster.md): a checklist covering:
  - the sidecar manifest for the opsman Agent, with checks and a rollback
  - checks for the running llm-d route, directly and through the gateway
  - when and how opsman moves to llm-d
  - a same-space comparison between routes

### Found

Measured on 2026-09-30 and recorded in ADR-0002. Nothing is fixed yet.

- Both running embedding routes serve the same Q8_0 file and returned the same
  vector for the same query. The comments that call the standalone route f16
  and the routes "two spaces" are wrong. These are
  `images/nomic-embed/Dockerfile`, `mcp/qdrant-mcp/README.md` and
  `releases/mcp-servers.yaml`.
- The published `nomic-embed:v1.18.1-4ccc0ff` is Q8_0 and amd64 only. That
  does not match what the repo's Dockerfile and workflow build (f16,
  multi-arch).
- The GGUF reports a 2048-token training context, so 8192 needs YaRN, which
  changes the vector space.
- The standalone embedder uses 293.5 MiB resident, where
  `releases/llama-cpp-embeddings.yaml` says about 75 MiB.

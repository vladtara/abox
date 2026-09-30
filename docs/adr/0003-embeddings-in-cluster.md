# ADR-0003: Serving embeddings to opsman in the cluster: sidecar, with llm-d as the shared route

- Status: Proposed
- Date: 2026-09-30
- Part of: [ADR-0001](0001-opsman-in-cluster-agent.md), sub-project 0
- Depends on: [ADR-0002](0002-embedding-model.md)

## Context

[ADR-0002](0002-embedding-model.md) picked nomic-embed-text-v1.5 Q8_0 on
llama.cpp. abox can serve it three ways:

1. **Standalone Deployment** (`releases/llama-cpp-embeddings.yaml`, exists).
   `llama-cpp-embeddings.llama-cpp:8090`, image `nomic-embed` with the GGUF
   baked in, llama.cpp b9438. `qdrant-mcp` uses it today.
2. **Sidecar** (does not exist yet). A llama.cpp container inside the
   consumer's pod, reached on `127.0.0.1`.
3. **llm-d** (`releases/llmd.yaml`, exists). The `llm-d-modelservice` chart
   v0.3.17 runs a decode Deployment on `ghcr.io/ggml-org/llama.cpp:server-b10920`
   with the `nomic-embed` image mounted as an image volume, plus an
   InferencePool and EPP (endpoint picker) from `inferencepool` v1.5.0.
   Embeddings go straight to the decode Service `llm-d-embedding.llm-d:8000`,
   because the EPP's scorers are built for generation traffic.

opsman runs as a kagent `BYO` Agent ([ADR-0001](0001-opsman-in-cluster-agent.md)).
In kagent 0.10.1, `Agent.spec.byo.deployment` has `extraContainers` and
`volumes`, so a sidecar and an image volume fit in the Agent itself. It has no
`initContainers`, so a native sidecar (an init container with
`restartPolicy: Always`) is not available, and nothing orders startup.

## Options

| | Sidecar | llm-d | Standalone |
|---|---|---|---|
| Path from opsman | `127.0.0.1`, no Service, no hop | Service, then decode pod | Service, then pod |
| Fails with | the opsman pod | on its own | on its own |
| Other consumers | no | yes | yes |
| Scales with | opsman replicas | its own replicas; the pool picks endpoints once there are several | by hand |
| Objects | one container, one volume | HelmRelease x2, InferencePool, EPP, InferenceObjective, CRDs, HTTPRoute | Deployment, Service, HTTPRoute |
| Reachable from | the pod only | cluster, and `/llmd` on the gateway | cluster, and `/llamacpp` on the gateway |
| Measured memory | not deployed | decode 16.3 MiB RSS, EPP 18.6 MiB | 293.5 MiB RSS |

## Decision

1. **opsman gets its own embedder as a sidecar.** A second container in the
   opsman Agent's pod, built exactly like llm-d's decode container:
   `ghcr.io/ggml-org/llama.cpp:server-b10920` plus the `nomic-embed` image as
   an image volume. Same binary and same GGUF as llm-d, so moving between the
   two changes nothing that affects vectors.
   - binds `127.0.0.1:8090`, so nothing outside the pod can call it
   - `--embeddings --ctx-size 4096 --ubatch-size 2048 --parallel 2`: two
     slots of 2048 tokens, enough for one loop
   - probes are `exec` running `curl` (present at `/usr/bin/curl` in that
     image), since the kubelet's `httpGet` would hit the pod IP, not localhost
   - opsman calls `http://127.0.0.1:8090`, and waits for `/health` to return
     200 before its first embed
2. **llm-d stays the shared route,** for any other consumer and for the day
   opsman runs more than one replica or needs a model llama.cpp cannot serve.
   opsman switches by changing `EMBEDDINGS_BASE_URL` to the decode Service,
   not the pool.
3. **The standalone Deployment is not opsman's.** It stays for `qdrant-mcp`
   until that moves; this ADR does not change it.

Why a sidecar for opsman:

- Its memory does not depend on another namespace's release.
- The embedder is not a cluster endpoint, so there is nothing new to protect.
- The model version is pinned in opsman's own manifest; changing it is one
  opsman release plus a re-embed.
- The cost is one small container per opsman pod, and opsman is expected to
  run one replica.

## Consequences

- Every opsman replica carries its own embedder. Past one replica, llm-d is
  cheaper.
- opsman must handle the embedder starting after it: retry `/health`, and
  report not-ready until it answers.
- The sidecar's memory at `--parallel 2 --ctx-size 4096` is not measured yet;
  [the cluster ToDo](../todo/embeddings-cluster.md) records it on first deploy.
- The sidecar has no `--metrics` scrape yet. That belongs to sub-project 4
  (satellites) or 5 (observability).
- `retrieval-agent` can read what opsman writes only while both pin the same
  GGUF digest ([ADR-0002](0002-embedding-model.md), "one collection, one
  model").

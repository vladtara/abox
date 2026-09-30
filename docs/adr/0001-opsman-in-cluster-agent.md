# ADR-0001: opsman, an operations agent that lives in the cluster

- Status: Proposed
- Date: 2026-09-30

## Context

We want one agent that runs the cluster from inside it:

- it runs in a loop, not once per task
- it discovers the environment on its own and keeps that picture current
- it improves its skills from what it finds
- it can start helper ("satellite") services for monitoring, logs and discovery
- it controls and manages the services in the cluster, and the cluster itself

The idea comes from [glapsfun/opsman](https://github.com/glapsfun/opsman), a
local-first orchestrator for coding agents. Its stance is "agents reason, the
shell proves": a POSIX shell kernel owns a state machine over an append-only
event journal, evidence, budgets and gates, and roles (discoverer, analyst,
planner, implementer, verifier, oracle) do the reasoning. Every step carries a
risk class R0 to R4, and R3/R4 wait for a recorded human approval. It runs per
task on a developer machine and never pushes.

abox already runs most of the parts such an agent needs:

| Need | In abox today |
|---|---|
| Agent runtime, A2A, UI | kagent 0.10.1. `Agent` has a `BYO` type for our own image, and `spec.skills` loads skills from git or OCI into `/skills` |
| LLM and MCP traffic | agentgateway |
| Text memory | Qdrant, `qdrant-mcp` (Go), embeddings from llama.cpp and llm-d |
| Cluster graph | Neo4j, used by `retrieval-agent` |
| Traces | `genai-collector` into Phoenix, MLflow and Jaeger |

kagent's own `Memory` CRD supports only Pinecone, so Qdrant memory stays ours.

## Decision

1. **opsman is a Go program that runs only in Kubernetes.** It is registered
   as a kagent `BYO` Agent, which gives it A2A, the kagent UI and kagent's
   skill loading. Its LLM calls go through agentgateway.
2. **Keep the reference's stance.** The model proposes; a deterministic Go
   kernel owns the loop, state, journal, budgets and risk gates, and refuses
   what the gates do not allow.
3. **Changes go through GitOps.** opsman changes the cluster by committing to
   `releases/` and tagging, the same path people use. R0 (read-only) runs on
   its own; R3/R4 wait for a human. RBAC is the ceiling the model cannot talk
   its way past.
4. **Stores.** Neo4j holds what exists and how it connects. Qdrant holds text
   memory: docs, events, past runs, skills. Phoenix and OTel hold a trace per
   loop iteration.
5. **Build it in sub-projects,** each with its own ADR:

   | # | Sub-project | ADR |
   |---|---|---|
   | 0 | Embeddings for opsman's memory | [0002](0002-embedding-model.md), [0003](0003-embeddings-in-cluster.md) |
   | 1 | Kernel: loop, state machine, journal, risk gates, RBAC | later |
   | 2 | Discovery: cluster into Neo4j, text into Qdrant | later |
   | 3 | Skills: load from git/OCI, propose changes, gate by evals | later |
   | 4 | Satellites: start helper services through GitOps | later |
   | 5 | Observability: traces per iteration | mostly in place (`genai-collector`) |

## Consequences

- Self-changing skills and self-started services are the largest risk. They
  pass the same gates as any other change; none of them get a shortcut.
- The only traffic leaving the cluster is LLM calls through agentgateway.
- Open for the kernel ADR: what triggers an iteration (timer, Kubernetes
  events, both), one replica or leader election, where the journal lives, and
  how a human approves an R3/R4 step from inside the cluster.

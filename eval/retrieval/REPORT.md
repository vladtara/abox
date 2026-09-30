# Agentic retrieval: official Qdrant MCP vs the default qdrant-mcp

Branch `feat/qdrant-official-mcp` on `vladtara/abox`, releases `v1.0.0` to `v1.2.0`, run on 2026-09-30 in Codespace `potential-pancake` (8 CPU, KinD, Kubernetes 1.37). Agent model: OpenAI `gpt-4.1-mini` for every run.

## Verdict

On this corpus the default toolset (the branch's `qdrant-mcp`, nomic-embed-text-v1.5 on llama.cpp) retrieves better and the agent answers better:

- **Hand-graded answers:** 21 of 23 correct, against 17 of 23 for the official toolset.
- **Hallucinations:** none, against 2 for the official toolset.

Most of the gap comes from all-MiniLM-L6-v2's 256-token window. 10 of the 45 manifests run past about 180 words, roughly where that window ends, and a question about a fact past the window ranks the right manifest first in 2 of 10 cases with MiniLM and 7 of 10 with nomic.

The agent narrows the raw gap because it reads all five hits. But when the right manifest is not in the top five, the official toolset either gives up or makes an answer up.

The largest single effect in the whole exercise was not the embedding model. It was one routing rule in the prompt, which on its own took the official toolset from 10 to 19 answers containing the expected facts.

## What was done

| Step | Result | Where |
|---|---|---|
| 1. Deploy from the `feat/llmd-embeddings` release | `make run` on the branch. The RSIP tracked `ghcr.io/den-vasyliev/abox/releases-llmd-embeddings` and applied `0.9.5`: 10 HelmReleases, 43 pods, all Ready | `bootstrap/` |
| 2. Add the official Qdrant MCP | MCPServer `qdrant-mcp-official`: `uvx mcp-server-qdrant@0.8.1` on `ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim`, collection `abox-minilm`, `QDRANT_SEARCH_LIMIT=5`, 1536Mi limit | `releases/mcp-servers.yaml` |
| 3. Model for retrieval-agent and k8s-agent | `default-model-config`: OpenAI `gpt-4.1-mini`, key read from Secret `abox-openai`, created by hand so the key never enters Git. Verified with a live `k8s-agent` call | `releases/kagent.yaml` |
| 4. Official MCP tools | `retrieval-agent` uses `qdrant-store` / `qdrant-find` | `releases/agent-retrieval.yaml` |
| 5. System prompt for the official MCP | A "Vector store" section on how those tools and MiniLM behave, plus the `v1.1.0` routing fix | `releases/agent-retrieval.yaml` |
| 6. Index with all-MiniLM-L6-v2 | 45 manifests into `abox-minilm`: 45 points, 384-d, 17.7 s | `mcp_eval.py ingest` |
| 7. Index with the default qdrant-mcp, switch the toolset | The same 45 into `abox-nomic`: 46 points, 768-d (retrieval-agent's 7.8k-char manifest split in two), 34.8 s. `v1.2.0` switches the agent to `vector_store` / `vector_find` | `mcp_eval.py ingest`, `releases/agent-retrieval.yaml` |
| 8. Compare and evaluate | This report | `eval/retrieval/` |

CI published each release to `ghcr.io/vladtara/abox/releases-qdrant-official-mcp`, and the cluster followed it through Flux:

| Release | Commit | Contents |
|---|---|---|
| `v1.0.0` | `3a53330` | official toolset |
| `v1.1.0` | `0ab34c0` | prompt routing fix |
| `v1.2.0` | `ac5bd81` | default toolset |

## Method

**Corpus.** Every object in `releases/` at `v1.0.0`, one document per object, with comments kept, since they hold the rationale most "why" questions ask about (`build_corpus.py`):

- 45 documents, 47k characters
- 10 of them longer than about 180 words

**Same data in both stores.** Each document was written through each server's own store tool with identical metadata (`mcp_eval.py ingest`). I didn't use the agent's own ingest because it paraphrases differently on every run, and then the two collections would not hold the same data.

**Held constant across runs:**

- model: `gpt-4.1-mini`
- five hits per search on both servers
- the prompt, identical except its "Vector store" section, which describes each server's real behaviour (scores, chunking, window, prefixes)
- a fresh A2A task per question
- an empty graph

**Questions.** 25 in `questions.yaml`: 5 shallow, 6 paraphrased, 3 "why", 10 deep (the answer lies past word 180 of its manifest), and 1 with no answer in the corpus. Each has the ids of the objects that answer it and the facts a correct answer must contain. The ground truth was checked mechanically against the corpus.

**Raw retrieval.** Each question went straight to `qdrant-find` or `vector_find`, with no LLM (`mcp_eval.py search`).

**Agentic retrieval.** Each question went to `retrieval-agent` over kagent's A2A API, with tool calls read from the task history (`agent_eval.py`). A keyword check scored each answer automatically. Then I read every answer against its source manifest and graded it by hand (`grades.yaml`). P5 and D10 are excluded from the hand grades: the agent answers them from its own system prompt without retrieving anything.

## Results

### Raw retrieval (no LLM)

| Subset | n | Hit@1 MiniLM | Hit@1 nomic | Hit@5 MiniLM | Hit@5 nomic | MRR MiniLM | MRR nomic |
|---|---|---|---|---|---|---|---|
| all | 24 | 0.38 | 0.62 | 0.83 | 0.96 | 0.540 | 0.751 |
| shallow | 5 | 0.60 | 0.80 | 0.80 | 1.00 | 0.667 | 0.867 |
| para | 6 | 0.67 | 0.50 | 0.83 | 0.83 | 0.722 | 0.625 |
| why | 3 | 0.00 | 0.33 | 1.00 | 1.00 | 0.500 | 0.583 |
| deep | 10 | 0.20 | 0.70 | 0.80 | 1.00 | 0.378 | 0.820 |
| search latency p50 | | 202 ms | 92 ms | | | | |

### Agentic retrieval

| Run | Keyword score | Hand: correct / partial / wrong | Hallucinations | Grade mean | Expected object retrieved | vector calls | graph calls | Latency p50 |
|---|---|---|---|---|---|---|---|---|
| A: official, v1.0.0 prompt | 10/25 | not graded | - | - | 9/24 | 0.60 | 1.08 | 3.4 s |
| A2: official, v1.1.0 prompt | 19/25 | 17 / 2 / 4 of 23 | 2 | 0.78 | 18/24 | 1.04 | 0.92 | 4.1 s |
| B: default, v1.2.0 | 22/25 | 21 / 1 / 1 of 23 | 0 | 0.93 | 21/24 | 1.04 | 1.04 | 3.6 s |

### Per question

Raw columns give the rank of the first expected object in the top five. Agent columns give the hand grade: 2 correct, 1 partial, 0 wrong, h hallucinated.

| Id | Type | Raw rank MiniLM | Raw rank nomic | Agent official | Agent default |
|---|---|---|---|---|---|
| S1 | shallow | 1 | 1 | 2 | 2 |
| S2 | shallow | miss | 1 | h | 2 |
| S3 | shallow | 1 | 1 | 2 | 2 |
| S4 | shallow | 3 | 3 | 1 | 2 |
| S5 | shallow | 1 | 1 | 2 | 2 |
| P1 | para | 1 | 1 | 2 | 2 |
| P2 | para | 1 | 4 | 2 | 2 |
| P3 | para | 1 | 1 | 2 | 2 |
| P4 | para | miss | miss | 0 | 0 |
| P5 | para | 3 | 1 | excl. | excl. |
| P6 | para | 1 | 2 | 2 | 2 |
| W1 | why | 2 | 2 | 2 | 2 |
| W2 | why | 2 | 1 | 2 | 2 |
| W3 | why | 2 | 4 | 2 | 2 |
| D1 | deep | 5 | 1 | 2 | 2 |
| D2 | deep | 4 | 1 | 1 | 1 |
| D3 | deep | miss | 2 | 0 | 2 |
| D4 | deep | miss | 5 | h | 2 |
| D5 | deep | 3 | 1 | 2 | 2 |
| D6 | deep | 3 | 1 | 2 | 2 |
| D7 | deep | 3 | 1 | 2 | 2 |
| D8 | deep | 1 | 1 | 2 | 2 |
| D9 | deep | 1 | 2 | 2 | 2 |
| D10 | deep | 3 | 1 | excl. | excl. |
| N1 | none | n/a | n/a | 2 | 2 |

## Findings

**1. The embedding window decides the deep questions.** MiniLM embeds the first 256 tokens of an entry and drops the rest without saying so. On deep questions its raw MRR is 0.378 against nomic's 0.820. The default server also chunks at 7,000 characters, so nothing is dropped. MiniLM still ranks a long manifest first when the question's terms also appear near its top, as in D8 (`InferencePool`) and D9 (the server's own name).

**2. The agent hides rank, not recall.** The agent reads all five hits, so a right answer at rank 3 to 5 was usually enough: D1, D5, D6, and D7 are all correct on the official toolset despite raw ranks of 3 to 5. What it cannot recover from is a miss. The two deep questions MiniLM missed outright (D3, D4) are the two the official toolset got wrong; D2 is partial on both toolsets (finding 5). That is why the agentic gap (0.78 against 0.93) is smaller than the raw one.

**3. Weak retrieval turns into confident wrong answers.** Both official-toolset hallucinations follow a miss:

- **S2:** the top hit was the other MCP server, and the agent asserted that it embeds with all-MiniLM into `abox-nomic`.
- **D4:** the right manifest never came back, and the agent offered "llama.cpp is lighter" as the reason. The documented reason is that vLLM's CPU worker was killed on the first forward pass.

The prompt's "cite the key" rule did not stop either one. The default toolset produced none.

**4. The prompt's routing mattered more than the model.** With the branch's original rule, "which" questions went to the graph. The graph was empty, so the agent stopped and asked the user whether to try the vector store, and S1 to S5 never made a vector call. Routing single-object questions to the vector store, and trying the other store before answering, took the official toolset from 10 to 19 keyword passes and from 9 to 18 expected-object retrievals. This fix is in `v1.1.0` and was used for both A2 and B.

**5. Two misses are shared, and they point at the corpus, not the model.**

- **P4 (the postRenderer):** neither store ranked the kagent HelmRelease in the top five. Both agents correctly said the answer was not found.
- **D2 (the Secret name):** `abox-openai` first appears at word 366 of 409 in the kagent HelmRelease. Both agents found `gpt-4.1-mini` in the retrieval-agent manifest's short comment instead, and reported the Secret as not found.

**6. Operating cost.**

| | Official `mcp-server-qdrant` | Default `qdrant-mcp` |
|---|---|---|
| Embedding | in-process (fastembed, onnxruntime) | llama.cpp Deployment, shared, 8 slots |
| Memory | about 307Mi RSS per live MCP session: kmcp's stdio adapter starts a fresh server process for each session (36 "Starting MCP server" lines in this pod's log). With 2 live sessions: 976Mi, and the cgroup reached its 1536Mi limit 3,279 times (`memory.events max`), with no OOM kill yet | 32Mi for the server, plus 297Mi for llama.cpp |
| Pod start | about 33 s: `uvx` installs 68 packages from PyPI, then fastembed fetches the model from HuggingFace. Needs egress on every start | image pull only |
| Ingest, 45 documents | 17.7 s | 34.8 s |
| Search p50 | 202 ms | 92 ms |
| Agent end-to-end p50 | 4.1 s | 3.6 s |

The one-process-per-session behaviour is consistent with the OOMKill the branch author hit at 256Mi: a single session already needs more than that, and each concurrent session adds another ~307Mi.

**7. Tool ergonomics.**

- **Official:** returns no scores, and the number of results is fixed by an env var. The stock tool descriptions talk about "memories", and long entries are truncated silently.
- **Default:** returns scores, chunk ids, and a `limit` parameter, and applies nomic's `search_query:` / `search_document:` prefixes itself. Its four extra health and tokenizer tools stay hidden through `toolNames`.

## Caveats

- **Sample size.** n=25, with one run per configuration, and `gpt-4.1-mini` is not deterministic. A difference of one or two answers is within noise. The robust signals are the deep-question gap and the hallucination difference.
- **Question design.** I wrote the questions knowing about the 256-token window, and 10 of 25 are deep on purpose. That matches this corpus, where the long manifests are the heavily commented ones, but it weights the comparison toward MiniLM's weakness.
- **Grading.** One grader (me). Every grade has its reason recorded in `grades.yaml`. The keyword score is kept alongside as a check: it had one false positive (D3 on the official toolset, which says "none found" but contains the words it looks for).
- **Graph not measured.** The graph was empty, so only `retrieval-agent`'s vector path was measured, not its graph path.
- **P6.** Its ground truth is ambiguous: the inference stack bypasses the endpoint picker for `/llmd/v1/embeddings` but uses it for `/llmd`. Both answers were graded correct.

## If the official server stays

None of these were tested here.

- **Fix the truncation.** Chunk entries before storing, or point `EMBEDDING_MODEL` at a long-context fastembed model; fastembed lists `nomic-ai/nomic-embed-text-v1.5`.
- **Run one process instead of one per session.** Use the server's own HTTP transport (`transportType: http`, `--transport streamable-http`, `FASTMCP_SERVER_HOST=0.0.0.0`) so a single process serves every session.
- **Bake an image.** Replacing `uvx` at start removes the PyPI and HuggingFace dependency from every pod start.
- **Set the tool descriptions.** Set `TOOL_STORE_DESCRIPTION` and `TOOL_FIND_DESCRIPTION`; the stock ones describe memories, not manifests.

## Reproduce

From the repo root, inside a pod that can reach the kagent namespace, with the cluster on `v1.0.0` or later. Ingest into empty collections: re-running it appends a second copy of every document.

```bash
python3 eval/retrieval/build_corpus.py > corpus.json
uv run eval/retrieval/mcp_eval.py ingest http://qdrant-mcp-official.kagent:3000/mcp qdrant-store corpus.json
uv run eval/retrieval/mcp_eval.py ingest http://qdrant-mcp.kagent:3000/mcp vector_store corpus.json
uv run eval/retrieval/mcp_eval.py search http://qdrant-mcp-official.kagent:3000/mcp qdrant-find eval/retrieval/questions.yaml raw-minilm.json
uv run eval/retrieval/mcp_eval.py search http://qdrant-mcp.kagent:3000/mcp vector_find eval/retrieval/questions.yaml raw-nomic.json
python3 -c 'import yaml,json; json.dump(yaml.safe_load(open("eval/retrieval/questions.yaml")), open("questions.json","w"))'
python3 eval/retrieval/agent_eval.py questions.json agent-default.json default   # on v1.2.0
python3 eval/retrieval/summarize.py eval/retrieval/results
```

The runs in `results/` were made from pod `eval-runner` in namespace `abox-eval`, on image `ghcr.io/astral-sh/uv:0.9.30-python3.12-bookworm-slim`.

## State left behind

- **Release.** The cluster is on release `1.2.0`, the default toolset. The RSIP only moves forward, so to go back to the official toolset, revert `ac5bd81` and tag `v1.3.0`.
- **Runner.** Namespace `abox-eval` and pod `eval-runner` are still running. They sit outside Flux, so delete them with `kubectl delete ns abox-eval`.
- **Collections.** `abox-minilm` and `abox-nomic` are still in Qdrant.

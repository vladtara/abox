# GenAI observability: plain OTel vs Phoenix vs MLflow

Branch `feat/genai-o11y` on `vladtara/abox`, releases `v1.3.0` to `v1.3.4`, run on 2026-09-30 in Codespace `potential-pancake` (8 CPU, KinD, Kubernetes 1.37). One OpenTelemetry Collector feeds the same traces to all three backends, so every difference below is a difference in the backend, not in the data.

## Verdict

**The data is the same in all three.** All 72 traces had the same span count in Phoenix and MLflow, and the 58 Jaeger still held matched too (it had already evicted the other 14). Token counts per LLM span agree too. Phoenix and MLflow computed the same cost for every kagent trace, $0.1275 for the 12 of them, from their own price tables. The differences are in how each tool reads GenAI spans, what it adds on top, and what it costs to run.

**Plain OTel (Jaeger 2.20, Prometheus, Grafana) is the transport and the SRE layer.** Jaeger v2 now has a GenAI view: for a span with GenAI semconv it renders the conversation and the token counts. It still has no cost, no sessions and no per-trace or per-project rollups. The demo's in-memory storage kept only about 25 minutes of traces. Its strength is metrics: span metrics and `gen_ai.client.operation.duration` put LLM latency into PromQL, dashboards and alerts, which neither GenAI tool is built for. The histograms are coarse, though: a 5.03 s LLM call showed up as a 9.75 s p95.

**Phoenix is the lighter GenAI tool** (~0.5 GiB plus Postgres). It gives projects, sessions, token and cost rollups, a filter language and a prompt playground. Its translation is weaker for non-OpenInference instrumentation:
- For kagent's Google ADK spans it shows the model and tokens but not the prompt.
- It labelled the load generator's `user_ask_agent` span as an LLM call, because that span carries `gen_ai.input.messages`.
- It leaves OpenLLMetry's workflow and task spans as `unknown`.

**MLflow translated the most** in this test:
- It parsed ADK's request into user message plus system instruction.
- It typed OpenLLMetry's WORKFLOW and TASK spans.
- It shows cost per span, and has judges, review queues, datasets and a prompt registry beside the traces.

It is also the heaviest and the most brittle:
- It sits flat at 1.85 GiB.
- It accepts only OTLP/HTTP with a numeric experiment-ID header.
- It never finishes a trace whose root span doesn't arrive (37 are stuck `IN_PROGRESS`).
- It marks a trace `OK` even when a tool inside it failed.

**Recommendation.** Keep one collector and fan out, as here:
- Plain OTel stays the system of record and the place for alerts.
- Add one GenAI backend for debugging agents.
- For kagent (Google ADK), MLflow shows more of what the model saw.
- For OpenLLMetry or OTel GenAI semconv, both work, and Phoenix is the cheaper to run.
- Choose MLflow when evaluation and prompt versioning should live next to the traces.

## What was done

| Step | Result | Where |
|---|---|---|
| 1. Get to know the otel, MLflow and Phoenix interfaces | All four UIs reachable through port-forwards, a guided tour below, 12 screenshots of the same traces | [Interfaces](#1-the-interfaces), `screens/` |
| 2. Get traces from Astronomy Shop's agent and from kagent agents | Astronomy Shop (OTel Demo 3.1.0) with its LangGraph agent, and kagent's `retrieval-agent` and `k8s-agent` on `gpt-4.1-mini`, all traced into the three backends | `releases/`, `kagent_traffic.py` |
| 3. Compare the three from a GenAI point of view | This report, with the evidence pulled from each backend's API | `collect.py`, `results/` |

CI published each release to `ghcr.io/vladtara/abox/releases-genai-o11y`, and Flux applied it:

| Release | Commit | Contents |
|---|---|---|
| `v1.3.0` | `1312fff` | otel-demo, genai-collector, MLflow, kagent tracing |
| `v1.3.1` | `c68026a` | 15m install timeout and retries (first installs stalled mid image pull) |
| `v1.3.2` | `563d6fd` | load-generator OTLP port fix (its spans never left the pod) |
| `v1.3.3` | `0c478b3` | memory for MLflow, Grafana after OOMKills |
| `v1.3.4` | `b556664` | the same for Jaeger, at the path its subchart reads |

## Setup

```
kagent agents (ADK + OpenLLMetry) ----------------------> genai-collector --+--> jaeger      (all kagent spans)
Astronomy Shop (OpenLLMetry) --> otel-demo collector --+-> jaeger           +--> phoenix     project per source
                                                       +-> genai-collector  +--> mlflow      experiment per source
                                                       +-> prometheus (span metrics, gen_ai.* metrics) --> grafana
```

- **genai-collector** (`releases/genai-collector.yaml`) routes on `k8s.namespace.name`. It tail-samples for Phoenix and MLflow: after 30 s it keeps a whole trace if any span has `gen_ai.operation.name`, `gen_ai.system`, `gen_ai.provider.name` or `traceloop.span.kind`.
  - Jaeger gets everything.
  - Phoenix and MLflow get only agent runs, not shop browsing or kagent UI polling.
- **Versions:** Jaeger 2.20.0 (in-memory, `MEMORY_MAX_TRACES=25000`), Prometheus and Grafana from demo chart 0.42.1, Phoenix 20.9.0 on Postgres, MLflow 3.16.1 on SQLite, collector-contrib 0.160.0.
- **Sources:**
  - kagent 0.10.1 agents: Google ADK spans, plus OpenLLMetry's OpenAI instrumentor, real `gpt-4.1-mini` calls.
  - The shop's `agent`: LangChain/LangGraph with Traceloop SDK 0.62.3. It replays LLM answers from VCR cassettes, so no model runs. The cassettes carry no `usage` block, so the shop has no token or cost data in any backend.

**Open the UIs.** In the Codespace:

```
kubectl -n otel-demo port-forward svc/frontend-proxy 8080:8080   # shop /, /jaeger/ui/, /grafana/, /feature/
kubectl -n phoenix port-forward svc/phoenix-svc 6006:6006        # login admin@localhost / admin
kubectl -n mlflow port-forward svc/mlflow 5000:5000              # no auth
```

From a laptop: `gh codespace ssh -c potential-pancake-7v47pgvv9prcprp5 -- -N -L 8080:localhost:8080 -L 6006:localhost:6006 -L 5000:localhost:5000`.

## 1. The interfaces

**Jaeger** ([search](screens/jaeger-1-search.jpg), [shop trace](screens/jaeger-2-shop-trace.jpg), [kagent trace](screens/jaeger-3-kagent-trace.jpg)):
- Search by service, span name, attributes and duration, with a duration scatter plot.
- The trace view is the usual timeline.
- The **GenAI View** toggle adds a GenAI tab to LLM spans: provider, model, the conversation (system, user, assistant, tool calls) and tokens.
- For kagent's ADK spans the tab shows model and tokens but no conversation. ADK puts the prompt in `gcp.vertex.agent.llm_request`, not in `gen_ai.input.messages`.
- Also: Compare, System Architecture, and Monitor (service performance).

**Grafana** ([LLM latency](screens/grafana-1-llm-latency.jpg)):
- Explore over Prometheus: the demo's span metrics (`traces_span_metrics_*`) give calls and latency per span name.
- The shop's Traceloop SDK also exports `gen_ai_client_operation_duration_seconds`, labelled with provider and model.
- The demo ships APM and span-metrics dashboards; none is GenAI-specific.

**Phoenix** ([projects](screens/phoenix-1-projects.jpg), [kagent traces](screens/phoenix-2-kagent-traces.jpg), [kagent trace](screens/phoenix-3-kagent-trace.jpg), [sessions](screens/phoenix-4-kagent-sessions.jpg), [shop trace](screens/phoenix-5-shop-trace.jpg)):
- **Projects:** one card per project with traces, sessions and p50 latency.
- **Inside a project:** Spans, Traces, Sessions, Metrics and Config tabs, trace volume and latency-percentile charts, and a filter bar (`span_kind == 'LLM'`, `name == 'ChatLLM.chat'`).
- **Trace view:** span tree with kind icons (llm, tool, agent), total cost and latency, and per span Info, Attributes, Events and Annotations.
- **Messages:** for the shop's LLM spans it renders system, user and assistant messages and tool calls. For kagent's it shows only the model name as input.
- **Beyond tracing:** Datasets & Experiments, Evaluators, Prompts, Playground and Dashboards.

**MLflow** ([kagent traces](screens/mlflow-1-kagent-traces.jpg), [kagent trace](screens/mlflow-2-kagent-trace.jpg), [shop trace](screens/mlflow-3-shop-trace.jpg)):
- **Layout:** an experiment per source, with Traces, Sessions (now a "group by session" toggle on Traces) and Overview.
- **Evaluation:** Judges, Review, Datasets and Evaluation runs.
- **Prompts & versions:** Playground, Prompts and Agent versions.
- **Trace view:** span tree with type icons, tokens and cost on every LLM span, and a Pretty view of inputs and outputs.
  - For kagent that view shows the user message and ADK's `systemInstruction`, which neither of the other two shows.
- **Extras:** "Assess" (feedback), "Analyze trace", and an MLflow Assistant side panel (beta).
- **Trace list:** its Input and Output columns are empty for kagent. The root span is the controller's HTTP span, which carries neither.

## 2. The traces

| | kagent (experiment 1, project `kagent`) | Astronomy Shop (experiment 2, project `astronomy-shop`) |
|---|---|---|
| Traffic | 12 A2A requests from `kagent_traffic.py`: 2 two-turn sessions, 2 forced tool errors | load generator's `ask_agent` task, about 3 a minute, 3 prompts |
| Traces kept for Phoenix and MLflow | 12 | 60 finished in MLflow (97 in Phoenix, counting those from before the load-generator fix) |
| Spans per trace | 6 to 12: controller HTTP, `invoke_agent`, ADK `generate_content`, `execute_tool` | 15: `user_ask_agent`, HTTP, workflow, LangGraph tasks, 2 `ChatLLM.chat`, tool, shop services |
| Tokens and cost | 313,513 tokens, $0.1275 | none (cassettes carry no usage) |

`results/traces.json` has one row per trace with each backend's view of it, `results/rollups.json` the project rollups, metrics and incident, and `results/footprint.json` the memory of each backend.

## 3. Comparison

| | Plain OTel (Jaeger + Prometheus/Grafana) | Phoenix 20.9 | MLflow 3.16 |
|---|---|---|---|
| OTLP in | gRPC and HTTP, no auth | gRPC and HTTP, API key required (auth on) | HTTP only, `x-mlflow-experiment-id` header required, ID only (no name) |
| Split by source | by service name | `openinference.project.name` resource attribute | experiment ID per exporter; IDs are assigned by the server, pinned here by a Job |
| Span typing | none, `gen_ai.operation.name` shown as a tag | OpenInference kinds, converted from GenAI semconv | span types from 10 conventions per its docs (GenAI semconv, OpenInference, OpenLLMetry, ADK, ...) |
| kagent LLM span shows | model and tokens (GenAI tab) | model and tokens | model, tokens, cost, user message and system instruction |
| Shop LLM span shows | conversation and tool call (GenAI tab) | conversation and tool call | conversation and tool call, shown twice |
| Token rollup per trace, project | no | yes | yes, per trace |
| Cost | no | yes, per span, trace, project, model | yes, per span and trace |
| Sessions | no | yes, `session.id` | yes, `mlflow.trace.session` |
| Metrics and alerting | yes, PromQL and Grafana alerting | Metrics tab and charts, no alerting | an Overview page, no alerting |
| Evaluation, prompts, playground | no | datasets, experiments, evaluators, prompts, playground | judges, review, datasets, evaluation runs, prompts, playground |
| Retention here | in memory, about 25 min at this load, lost on restart | Postgres | SQLite on a PVC |
| Memory here | Jaeger 427 MiB, Prometheus 478 MiB, Grafana 491 MiB | 491 MiB + Postgres 62 MiB | 1,880 MiB |

### Same data underneath

For each trace ID MLflow finished, `collect.py` fetched the same ID from Jaeger and Phoenix:
- **Span counts:** Phoenix and MLflow agree on all 72, and Jaeger on the 58 it still held.
- **Tokens:** Phoenix's trace token totals equal MLflow's `mlflow.trace.tokenUsage` for all 12 kagent traces.
- **Cost:** Phoenix's cost equals MLflow's to 1e-9.

Neither computes the cost from anything the trace carries: both price `gpt-4.1-mini` from their own tables, and they agree.

### How spans are understood

The same 15-span shop trace (`35214235`), grouped by each backend:

| Jaeger `gen_ai.operation.name` | Phoenix kind | MLflow type |
|---|---|---|
| invoke_workflow 1, invoke_agent 2, execute_task 3, chat 2, execute_tool 1, none 6 | llm 3, agent 2, tool 1, unknown 9 | WORKFLOW 1, AGENT 2, TASK 3, CHAT_MODEL 2, TOOL 1, none 6 |

MLflow keeps OpenLLMetry's workflow and task structure. Phoenix calls it unknown, and counts one LLM span too many: the load generator's `user_ask_agent`, a plain client span that carries `gen_ai.input.messages`. All 60 finished shop traces have an "llm" root in Phoenix because of it.

For a kagent trace the three agree: 2 agent, 2 LLM, 1 tool, 3 plain spans.

### Prompts and messages

What each backend shows for the input of an LLM span:

| | kagent (ADK, `gcp.vertex.agent.llm_request`) | Astronomy Shop (`gen_ai.input.messages`) |
|---|---|---|
| Jaeger GenAI tab | model and tokens only | system, user, assistant, tool call |
| Phoenix | model name only; no `llm.input_messages` is built | system, user, assistant, tool call |
| MLflow | user message and system instruction, parsed | user and tool call, each rendered twice ([screenshot](screens/mlflow-3-shop-trace.jpg)) |

The kagent case matters most: it is a real model call, and only MLflow shows what the model was sent. In Jaeger and Phoenix that prompt is only in a raw JSON attribute.

At trace level, neither GenAI tool shows the question or answer for kagent (0 of 12). The root span is kagent-controller's `POST /api/a2a/...`, which carries neither. For the shop, MLflow takes the request preview from the root's `gen_ai.input.messages` (60 of 60) but has no response preview (0 of 60).

### Tokens and cost

One trace dominates the cost, and both GenAI tools surface it:
- **The trace:** `6a10194b`, k8s-agent asked "Which namespaces have pods that are not Running or Completed?"
- **What happened:** its second LLM call sent 230,420 input tokens, the full pod list of every namespace returned by `k8s_get_resources`.
- **What it cost:** $0.0935, 73% of all kagent spend in this run.

MLflow shows it per span in the trace view. Phoenix shows it in the trace header and the project's cost and top-models rollups.

In Jaeger, the per-span tokens are visible only once the span is opened, and there's no cost, so the same outlier takes a manual search.

### Sessions

kagent stamps the A2A `contextId` as `session.id`. Both GenAI tools pick it up:
- The IDs match in all 12 traces.
- Both two-turn conversations are grouped: Phoenix counts 10 sessions for 12 traces.

Jaeger has the attribute but no session view.

### Incident: a slow LLM

The shop's `aiSlowResponse` flag was set to `5sec` from 19:41:52 to 19:46:54 UTC. Each backend was then asked for the `ChatLLM.chat` latency, before (the same length of time just prior) against during:

| | before | during |
|---|---|---|
| Phoenix, `spanLatencyMsQuantile` on `name == 'ChatLLM.chat'` | p50 23.6 ms, p95 35.7 ms, n 34 | p50 5,034 ms, p95 5,044 ms, n 14 |
| Jaeger, spans from a search | p50 24.6 ms (n 14; the older ones already evicted) | p50 5,034 ms, p95 5,047 ms, n 14 |
| MLflow, trace duration (2 LLM calls per run) | p50 0.144 s, 17 traces | p50 10.17 s, 11 traces |
| Prometheus, span-metrics p95 per minute | 48 ms | 9,500 to 9,750 ms (19:43 to 19:47) |

- **All four see the incident.**
- **Only Prometheus can alert on it,** and its numbers are wrong by nearly 2x. The span-metrics histogram has buckets at 5,000 and 10,000 ms, so a 5,034 ms call interpolates to 9.75 s.
- **Phoenix and Jaeger give the exact span latency.**
- **MLflow's numbers here are trace durations,** two LLM calls per run.

### Tool errors

Two requests forced a failing tool: invalid Cypher, and logs of a pod that doesn't exist. In each backend the tool span is an error, with the MCP error text:
- Jaeger: `error=true`, `otel.status_description`.
- Phoenix: `statusCode ERROR`, error count 1 on the trace.
- MLflow: span status `ERROR`.

MLflow's trace state stays `OK`, because it takes the state from the root span, and the agent recovered and answered. Filtering MLflow's trace list for failed traces misses both.

Every other kagent span is `UNSET`, never `OK`: in this run kagent set a status only on the two failing tool spans.

### Getting data in

- **Jaeger** takes OTLP from anything.
- **Phoenix** with auth on (the chart default here) rejects OTLP without a system API key: 401 without it, 200 with it. The key is created through its GraphQL API and kept in Secret `phoenix-otlp`.
- **MLflow:**
  - It has no OTLP/gRPC receiver.
  - It needs one exporter per experiment, because the ID travels in a header.
  - It needs the ID itself, which the server assigns. A Job (`releases/mlflow.yaml`) creates the experiments in a fixed order and fails if the IDs are not the ones the collector sends.
  - MLflow 3.14+ checks the Host header against `--allowed-hosts`, so the collector's cluster DNS name has to be listed (upstream's first attempt got 403s).

### Problems found on the way

- **Load generator spans never exported.** In demo chart 0.42.1, the load generator uses the gRPC OTLP exporter but is pointed at `:4318`. So `user_ask_agent`, the root of every agent trace, was missing. Jaeger and Phoenix showed the traces with a missing parent. MLflow waits for a root span before it finishes a trace, so it kept 37 of them `IN_PROGRESS` for good. Fixed in `v1.3.2` with an `envOverrides` to `:4317`.
- **Spans lost during the incident, cause not found.** During the incident, 10 of 11 slow shop traces arrived with 7 to 14 spans instead of 15. 8 of them lacked the second LLM call, and most lacked the outer workflow spans. The counts are identical in all three backends and no trace split off, so the spans were lost in the agent or the demo collector, before any backend.
- **OOMKills:**
  - MLflow sits flat at ~1.85 GiB (eight Python processes of 230 to 250 MiB). A `kubectl exec ... python` beside them pushed it past 2 GiB.
  - Jaeger (600 MiB) died on a 3 h, 20,000-trace search and lost everything it held.
  - Grafana (300 MiB) died rendering an Explore query.
  - The MLflow and Jaeger kills were caused by my own probes, but they show how little headroom the defaults leave.
  - `v1.3.3` and `v1.3.4` raise the limits to 3 GiB, 1 GiB and 512 MiB.
- **First installs stalled.** The otel-demo and MLflow installs hit Helm's 5-minute wait during their first image pull (MLflow's image alone took 3m43s). `v1.3.1` gives them 15 minutes and 3 retries.

## Reproduce

```
kubectl -n kagent port-forward svc/kagent-controller 18083:8083 &
python3 eval/o11y/kagent_traffic.py
INCIDENT=<start>/<end> eval/o11y/collect.sh eval/o11y/results     # needs jq; start/end as ISO UTC
kubectl -n otel-demo port-forward svc/frontend-proxy 8080:8080 &   # plus the three from collect.sh
PHOENIX_API_KEY=... python eval/o11y/screens.py eval/o11y/results eval/o11y/screens   # needs playwright
```

Flip `aiSlowResponse` in the flagd UI at `/feature/`, or in the flagd-ui sidecar's `/app/data/demo.flagd.json`.

## Limits

- **Small sample:** 12 kagent traces and 60 finished shop traces, from one run.
- **No shop token data:** the shop's LLM is replayed, so it has no tokens, cost or real latency. Those comparisons rest on kagent alone.
- **Not exercised:** evaluation, prompt management, playgrounds, judges and the assistants were not used. They are listed from the UIs only.
- **Jaeger's retention is this deployment's, not Jaeger's:** the demo configures in-memory storage. A real deployment would use a persistent store.
- **Pinned versions:** the translation results hold for these versions only. Phoenix, MLflow and Jaeger all changed their GenAI support within the last few releases.

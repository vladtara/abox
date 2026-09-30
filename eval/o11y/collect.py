"""Follow the same GenAI traces through Jaeger, Phoenix and MLflow.

MLflow picks the traces: every one in the kagent and astronomy-shop
experiments that finished (state OK). Each trace ID is then fetched from Jaeger
and Phoenix, and per backend the script records what that backend made of it:
span count, how spans are typed, LLM spans with their model and tokens, the
trace's token and cost rollup, session, and root input/output. It also saves
the project rollups Phoenix computes and the GenAI metrics Prometheus holds.

Run next to port-forwards to the four UIs (eval/o11y/collect.sh sets them up):

  PHOENIX_API_KEY=... [INCIDENT=<start>/<end>] python3 eval/o11y/collect.py eval/o11y/results
"""

import collections
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

JAEGER = os.environ.get("JAEGER", "http://localhost:16686/jaeger/ui/api")
PHOENIX = os.environ.get("PHOENIX", "http://localhost:6006")
MLFLOW = os.environ.get("MLFLOW", "http://localhost:5000")
PROM = os.environ.get("PROM", "http://localhost:9090")
KEY = os.environ["PHOENIX_API_KEY"]
EXPERIMENTS = {"1": "kagent", "2": "astronomy-shop"}
OUT = sys.argv[1]


def call(url, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def gql(query, **variables):
    out = call(f"{PHOENIX}/graphql", {"query": query, "variables": variables}, {"authorization": f"Bearer {KEY}"})
    if out.get("errors"):
        raise RuntimeError(out["errors"])
    return out["data"]


def clip(v, n=160):
    return v if v is None or len(v) <= n else v[:n] + "..."


def num(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def jaeger(tid):
    try:
        data = call(f"{JAEGER}/traces/{tid}")["data"]
    except urllib.error.HTTPError:
        return None
    if not data:
        return None
    spans = data[0]["spans"]
    procs = data[0]["processes"]
    ids = {s["spanID"] for s in spans}
    llm = []
    for s in spans:
        t = {x["key"]: x["value"] for x in s["tags"]}
        if t.get("gen_ai.operation.name") in ("chat", "text_completion", "generate_content") or "gen_ai.usage.input_tokens" in t:
            llm.append({"name": s["operationName"], "model": t.get("gen_ai.request.model"),
                        "in": num(t.get("gen_ai.usage.input_tokens")), "out": num(t.get("gen_ai.usage.output_tokens"))})
    return {
        "spans": len(spans),
        "services": sorted({procs[s["processID"]]["serviceName"] for s in spans}),
        "typed_as": dict(collections.Counter(
            next((x["value"] for x in s["tags"] if x["key"] == "gen_ai.operation.name"), "(none)") for s in spans)),
        "llm_spans": llm,
        "roots": [s["operationName"] for s in spans if not s["references"]],
        "missing_parents": sum(1 for s in spans if s["references"] and s["references"][0]["spanID"] not in ids),
        "error_spans": sum(any(x["key"] == "error" and x["value"] for x in s["tags"]) for s in spans),
        "duration_ms": max(s["startTime"] + s["duration"] for s in spans) / 1000 - min(s["startTime"] for s in spans) / 1000,
    }


PROJECTS = {e["node"]["name"]: e["node"]["id"] for e in gql("{ projects { edges { node { id name } } } }")["projects"]["edges"]}

PHX_TRACE = """
query($pid: ID!, $tid: ID!) { node(id: $pid) { ... on Project { trace(traceId: $tid) {
  numSpans latencyMs errorCount
  spanCountsByKind { spanKind count }
  costSummary { prompt { tokens cost } completion { tokens cost } total { tokens cost } }
  session { sessionId numTraces }
  rootSpan { name spanKind input { value } output { value }
             cumulativeTokenCountPrompt cumulativeTokenCountCompletion cumulativeTokenCountTotal }
  spans(first: 200) { edges { node { name spanKind parentId tokenCountPrompt tokenCountCompletion attributes } } }
} } } }"""


def phoenix(project, tid):
    t = gql(PHX_TRACE, pid=PROJECTS[project], tid=tid)["node"]["trace"]
    if not t:
        return None
    spans = [e["node"] for e in t["spans"]["edges"]]
    llm = []
    for s in spans:
        if s["spanKind"] == "llm":
            a = json.loads(s["attributes"] or "{}")
            llm.append({"name": s["name"], "model": a.get("llm", {}).get("model_name"),
                        "in": s["tokenCountPrompt"], "out": s["tokenCountCompletion"]})
    root = t["rootSpan"] or {}
    return {
        "spans": t["numSpans"],
        "typed_as": {c["spanKind"]: c["count"] for c in t["spanCountsByKind"]},
        "llm_spans": llm,
        "tokens": {"prompt": root.get("cumulativeTokenCountPrompt"), "completion": root.get("cumulativeTokenCountCompletion"),
                   "total": root.get("cumulativeTokenCountTotal")},
        "cost_usd": (t["costSummary"] or {}).get("total", {}).get("cost"),
        "session": (t["session"] or {}).get("sessionId"),
        "session_traces": (t["session"] or {}).get("numTraces"),
        "root": root.get("name"),
        "root_kind": root.get("spanKind"),
        "input": clip((root.get("input") or {}).get("value")),
        "output": clip((root.get("output") or {}).get("value")),
        "latency_ms": t["latencyMs"],
        "error_spans": t["errorCount"],
    }


def mlflow_traces(eid):
    body = {"locations": [{"type": "MLFLOW_EXPERIMENT", "mlflow_experiment": {"experiment_id": eid}}], "max_results": 500}
    return call(f"{MLFLOW}/api/3.0/mlflow/traces/search", body).get("traces", [])


def mlflow(info):
    spans = call(f"{MLFLOW}/ajax-api/3.0/mlflow/get-trace-artifact?request_id={info['trace_id']}")["spans"]
    meta = info.get("trace_metadata") or {}
    llm = []
    for s in spans:
        a = s.get("attributes", {})
        if json.loads(a.get("mlflow.spanType", '""')) in ("LLM", "CHAT_MODEL"):
            usage = json.loads(a.get("mlflow.chat.tokenUsage", "{}"))
            llm.append({"name": s["name"], "model": json.loads(a.get("mlflow.llm.model", "null")),
                        "in": usage.get("input_tokens"), "out": usage.get("output_tokens")})
    return {
        "spans": len(spans),
        "typed_as": dict(collections.Counter(json.loads(s.get("attributes", {}).get("mlflow.spanType", '"(none)"')) for s in spans)),
        "llm_spans": llm,
        "tokens": json.loads(meta.get("mlflow.trace.tokenUsage", "null")),
        "cost_usd": json.loads(meta.get("mlflow.trace.cost", "null")),
        "session": meta.get("mlflow.trace.session"),
        "state": info["state"],
        "error_spans": sum(s.get("status", {}).get("code") == "STATUS_CODE_ERROR" for s in spans),
        "input": clip(info.get("request_preview")),
        "output": clip(info.get("response_preview")),
        "duration": info.get("execution_duration"),
    }


rows, states = [], {}
for eid, project in EXPERIMENTS.items():
    infos = mlflow_traces(eid)
    states[project] = dict(collections.Counter(i["state"] for i in infos))
    for info in infos:
        if info["state"] != "OK":
            continue
        tid = info["trace_id"].removeprefix("tr-")
        rows.append({"project": project, "trace_id": tid, "mlflow": mlflow(info),
                     "phoenix": phoenix(project, tid), "jaeger": jaeger(tid)})

PHX_PROJECT = """
query($pid: ID!) { node(id: $pid) { ... on Project {
  name traceCount recordCount tokenCountTotal tokenCountPrompt tokenCountCompletion sessionCount
  p50: latencyMsQuantile(probability: 0.5) p99: latencyMsQuantile(probability: 0.99)
  costSummary { total { cost } }
} } }"""
projects = [gql(PHX_PROJECT, pid=PROJECTS[p])["node"] for p in EXPERIMENTS.values()]

prom = {}
for q in ("gen_ai_client_operation_duration_seconds_count", "gen_ai_client_token_usage_sum",
          'histogram_quantile(0.95, sum by (le, span_name) (rate(traces_span_metrics_duration_milliseconds_bucket{service_name="agent"}[15m])))'):
    r = call(f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": q}))["data"]["result"]
    prom[q] = [{"labels": x["metric"], "value": x["value"][1]} for x in r]

incident = None
if os.environ.get("INCIDENT"):
    # INCIDENT="<start>/<end>", ISO UTC, the window aiSlowResponse was on. Each
    # backend is asked the same thing: how slow did the shop's LLM span get,
    # inside the window against the same length of time just before it.
    start, end = os.environ["INCIDENT"].split("/")
    ts = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    t0, t1 = ts(start), ts(end)
    before = (t0 - (t1 - t0), t0)
    windows = {"before": before, "during": (t0, t1)}
    phx_q = """query($pid: ID!, $tr: TimeRange!) { node(id: $pid) { ... on Project {
      n: recordCount(timeRange: $tr, filterCondition: "name == 'ChatLLM.chat'")
      p50: spanLatencyMsQuantile(probability: 0.5, timeRange: $tr, filterCondition: "name == 'ChatLLM.chat'")
      p95: spanLatencyMsQuantile(probability: 0.95, timeRange: $tr, filterCondition: "name == 'ChatLLM.chat'") } } }"""
    incident = {"window": os.environ["INCIDENT"], "phoenix": {}, "jaeger": {}, "mlflow": {}, "prometheus": {}}
    for label, (a, b) in windows.items():
        incident["phoenix"][label] = gql(phx_q, pid=PROJECTS["astronomy-shop"], tr={"start": a.isoformat(), "end": b.isoformat()})["node"]
        us = lambda d: int(d.timestamp() * 1e6)
        found = call(f"{JAEGER}/traces?" + urllib.parse.urlencode(
            {"service": "agent", "operation": "ChatLLM.chat", "start": us(a), "end": us(b), "limit": 1000}))["data"]
        durs = sorted(s["duration"] / 1000 for t in found for s in t["spans"] if s["operationName"] == "ChatLLM.chat")
        incident["jaeger"][label] = {"n": len(durs), "p50": durs[len(durs) // 2] if durs else None,
                                     "p95": durs[int(len(durs) * 0.95)] if durs else None}
        ms = lambda d: int(d.timestamp() * 1000)
        body = {"locations": [{"type": "MLFLOW_EXPERIMENT", "mlflow_experiment": {"experiment_id": "2"}}], "max_results": 500,
                "filter": f"trace.timestamp_ms >= {ms(a)} AND trace.timestamp_ms < {ms(b)}"}
        got = call(f"{MLFLOW}/api/3.0/mlflow/traces/search", body).get("traces", [])
        secs = sorted(float(t["execution_duration"].rstrip("s")) for t in got if t.get("execution_duration"))
        incident["mlflow"][label] = {"traces": len(got), "ok": sum(t["state"] == "OK" for t in got),
                                     "p50_s": secs[len(secs) // 2] if secs else None}
    r = call(f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode({
        "query": 'histogram_quantile(0.95, sum by (le) (rate(traces_span_metrics_duration_milliseconds_bucket'
                 '{service_name="agent", span_name="ChatLLM.chat"}[3m])))',
        "start": before[0].timestamp(), "end": (t1 + (t1 - t0)).timestamp(), "step": 60}))["data"]["result"]
    incident["prometheus"]["chatllm_p95_ms_per_minute"] = [
        (datetime.datetime.fromtimestamp(v[0], datetime.UTC).strftime("%H:%M"), v[1]) for v in (r[0]["values"] if r else [])]

os.makedirs(OUT, exist_ok=True)
json.dump(rows, open(f"{OUT}/traces.json", "w"), indent=1)
json.dump({"mlflow_states": states, "phoenix_projects": projects, "prometheus": prom, "incident": incident},
          open(f"{OUT}/rollups.json", "w"), indent=1)
print(f"{len(rows)} traces; mlflow states {states}")

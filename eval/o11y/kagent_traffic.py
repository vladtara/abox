"""Send the 12 kagent requests behind eval/o11y's kagent traces.

Two two-turn sessions (the second turn reuses the first's contextId), a
question whose tool output blows up the prompt, and two tool calls that fail
on purpose. Needs kagent's controller port-forwarded:

  kubectl -n kagent port-forward svc/kagent-controller 18083:8083 &
  python3 eval/o11y/kagent_traffic.py
"""

import json
import os
import time
import urllib.request
import uuid

BASE = os.environ.get("KAGENT_A2A", "http://localhost:18083/api/a2a/kagent")
RUNS = [
    ("retrieval-agent", "s1", "Which image does the qdrant-mcp MCPServer run?"),
    ("retrieval-agent", "s1", "And what memory limit does it have?"),
    ("retrieval-agent", None, "Which HelmReleases depend on kagent-crds?"),
    ("k8s-agent", None, "List the pods in namespace mlflow with their status."),
    ("k8s-agent", None, "Is the opentelemetry-demo HelmRelease in namespace otel-demo ready? Show its conditions."),
    ("k8s-agent", None, "Show me the last 20 log lines of pod does-not-exist-0 in namespace mlflow."),
    ("k8s-agent", None, "Which namespaces have pods that are not Running or Completed?"),
    ("retrieval-agent", "s2", "What port does the phoenix Service expose for OTLP gRPC?"),
    ("retrieval-agent", "s2", "Is that the port genai-collector sends to?"),
    ("retrieval-agent", None, "Which HelmRelease pins chart version 1.19.1?"),
    ("retrieval-agent", None, "Run exactly this Cypher with read-cypher and show me the raw result: MATCH (n RETURN n LIMIT 1"),
    ("k8s-agent", None, "Call the pod logs tool for pod does-not-exist-0 in namespace mlflow and show me exactly what the tool returned."),
]

ctx = {}
for agent, session, text in RUNS:
    msg = {"role": "user", "messageId": str(uuid.uuid4()), "parts": [{"kind": "text", "text": text}]}
    if session in ctx:
        msg["contextId"] = ctx[session]
    body = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send", "params": {"message": msg}}
    req = urllib.request.Request(f"{BASE}/{agent}/", data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    t = time.time()
    res = json.load(urllib.request.urlopen(req, timeout=180)).get("result", {})
    if session:
        ctx.setdefault(session, res.get("contextId"))
    parts = [p.get("text", "") for a in (res.get("artifacts") or []) for p in a.get("parts", [])]
    print(f"{agent} ctx={res.get('contextId')} {time.time() - t:.1f}s | {text}\n  -> {' '.join(parts)[:200]}", flush=True)

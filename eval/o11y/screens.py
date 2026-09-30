"""Screenshot the same traces in Jaeger, Grafana, Phoenix and MLflow.

Trace IDs come from collect.py's traces.json: the kagent trace with the most
LLM calls and the slowest astronomy-shop trace. Needs the same port-forwards as
collect.sh plus frontend-proxy on 8080 (Grafana), and Playwright:

  PHOENIX_API_KEY=... [ONLY=grafana,mlflow] python eval/o11y/screens.py eval/o11y/results eval/o11y/screens

ONLY retakes the shots whose names start with one of its prefixes. Jaeger
keeps traces in memory, so its shots only work while it still holds them.
"""

import json
import os
import sys
import urllib.parse

from playwright.sync_api import sync_playwright

RESULTS, OUT = sys.argv[1], sys.argv[2]
KEY = os.environ["PHOENIX_API_KEY"]
ONLY = [x for x in os.environ.get("ONLY", "").split(",") if x]
rows = json.load(open(f"{RESULTS}/traces.json"))
kagent = max((r for r in rows if r["project"] == "kagent"), key=lambda r: len(r["mlflow"]["llm_spans"]))
shop = max((r for r in rows if r["project"] == "astronomy-shop"), key=lambda r: r["jaeger"]["duration_ms"] if r["jaeger"] else 0)
print("kagent", kagent["trace_id"], "shop", shop["trace_id"])

prom = ('histogram_quantile(0.95, sum by (le, span_name) (rate(traces_span_metrics_duration_milliseconds_bucket'
        '{service_name="agent", span_name=~"ChatLLM.chat|astronomy_shop_agent_workflow.workflow"}[3m])))')
explore = {"p": {"datasource": "webstore-metrics", "queries": [{"refId": "A", "expr": prom}], "range": {"from": "now-45m", "to": "now"}}}
grafana = "http://localhost:8080/grafana/explore?schemaVersion=1&orgId=1&panes=" + urllib.parse.quote(json.dumps(explore))

os.makedirs(OUT, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    phx = browser.new_context(viewport={"width": 1600, "height": 1000},
                              extra_http_headers={"authorization": f"Bearer {KEY}"}).new_page()

    def shot(pg, name, url, wait=4000, click=None):
        if ONLY and not any(name.startswith(o) for o in ONLY):
            return
        pg.goto(url, wait_until="load")
        pg.wait_for_timeout(wait)
        got_it = pg.get_by_role("button", name="Got it")
        if got_it.count():
            got_it.first.click()
            pg.wait_for_timeout(500)
        if click:
            loc = pg.get_by_text(click, exact=False).first
            if loc.count():
                loc.click()
                pg.wait_for_timeout(2500)
        pg.screenshot(path=f"{OUT}/{name}.jpg", type="jpeg", quality=70)
        print("saved", name, pg.url)

    j = "http://localhost:16686/jaeger/ui"
    shot(page, "jaeger-1-search", f"{j}/search?service=agent&lookback=1h&limit=20")
    shot(page, "jaeger-2-shop-trace", f"{j}/trace/{shop['trace_id']}", click="ChatLLM.chat")
    shot(page, "jaeger-3-kagent-trace", f"{j}/trace/{kagent['trace_id']}", click="generate_content")
    shot(page, "grafana-1-llm-latency", grafana, wait=20000)

    pids = {}
    phx.goto("http://localhost:6006/graphql")
    for e in json.loads(phx.evaluate(
            "async () => JSON.stringify(await (await fetch('/graphql', {method: 'POST', headers: {'content-type': "
            "'application/json'}, body: JSON.stringify({query: '{ projects { edges { node { id name } } } }'})})).json())"
    ))["data"]["projects"]["edges"]:
        pids[e["node"]["name"]] = e["node"]["id"]
    ph = "http://localhost:6006"
    shot(phx, "phoenix-1-projects", f"{ph}/projects")
    shot(phx, "phoenix-2-kagent-traces", f"{ph}/projects/{pids['kagent']}")
    shot(phx, "phoenix-3-kagent-trace", f"{ph}/projects/{pids['kagent']}/traces/{kagent['trace_id']}", click="generate_content")
    shot(phx, "phoenix-4-kagent-sessions", f"{ph}/projects/{pids['kagent']}/sessions")
    shot(phx, "phoenix-5-shop-trace", f"{ph}/projects/{pids['astronomy-shop']}/traces/{shop['trace_id']}", click="ChatLLM.chat")

    m = "http://localhost:5000/#/experiments"
    shot(page, "mlflow-1-kagent-traces", f"{m}/1/traces", wait=6000)
    shot(page, "mlflow-2-kagent-trace", f"{m}/1/traces?selectedEvaluationId=tr-{kagent['trace_id']}", wait=6000, click="generate_content")
    shot(page, "mlflow-3-shop-trace", f"{m}/2/traces?selectedEvaluationId=tr-{shop['trace_id']}", wait=6000, click="ChatLLM.chat")
    browser.close()

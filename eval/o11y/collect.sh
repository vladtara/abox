#!/usr/bin/env bash
# Port-forward Jaeger, Phoenix, MLflow and Prometheus, run collect.py, and
# record each backend's memory from the kubelet stats (no metrics-server here).
#
#   eval/o11y/collect.sh eval/o11y/results
set -euo pipefail
OUT=${1:-eval/o11y/results}
cd "$(dirname "$0")/../.."

pids=()
trap 'kill "${pids[@]}" 2>/dev/null' EXIT
forward() { kubectl -n "$1" port-forward "svc/$2" "$3" >/dev/null 2>&1 & pids+=($!); }
forward otel-demo jaeger 16686:16686
forward phoenix phoenix-svc 6006:6006
forward mlflow mlflow 5000:5000
forward otel-demo prometheus 9090:9090
sleep 5

PHOENIX_API_KEY=$(kubectl -n observability get secret phoenix-otlp -o jsonpath='{.data.PHOENIX_API_KEY}' | base64 -d) \
  python3 eval/o11y/collect.py "$OUT"

for n in $(kubectl get nodes -o name); do
  kubectl get --raw "/api/v1/nodes/${n#node/}/proxy/stats/summary"
done | jq -s '[.[].pods[] | select(.podRef.namespace | test("^(otel-demo|phoenix|mlflow|observability)$"))
  | select(.podRef.name | test("^(jaeger|prometheus|grafana|otel-collector|phoenix|mlflow-[0-9a-f]|genai-collector)"))
  | {pod: .podRef.name, ns: .podRef.namespace, mem_mib: ((.memory.workingSetBytes // 0) / 1048576 | floor)}]' \
  > "$OUT/footprint.json"
jq -c '.[]' "$OUT/footprint.json"

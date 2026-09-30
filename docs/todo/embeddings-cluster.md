# ToDo: serve embeddings to opsman in the cluster (sidecar and llm-d)

For the agent working on this repo. The decision behind it is
[ADR-0003](../adr/0003-embeddings-in-cluster.md); the model is
[ADR-0002](../adr/0002-embedding-model.md).

- **Part A** adds the sidecar to the opsman Agent. It needs sub-project 1: an
  opsman image that serves A2A the way kagent `BYO` agents must.
- **Part B** checks the llm-d route that already runs, and says how opsman
  moves to it.

Rules while doing it:

- Changes land through GitOps: edit `releases/`, open a PR, tag after merge.
  No `kubectl apply`, `edit` or `patch` against the cluster.
- `kubectl` below is read-only, plus `port-forward` and `exec` for checks.
- Stop at the first check that fails and report its output.

The commands assume the kube context of the abox cluster, and that
`KUBECONFIG` points at it.

## Part A: sidecar in the opsman pod

**Done when** every box in A.2 is checked, and the sidecar's memory is recorded
in ADR-0003.

### A.1 Change the manifest

- [ ] In the opsman Agent manifest (`releases/opsman.yaml`, created by
  sub-project 1), add the image volume, the embedder container and the env
  var:

  ```yaml
  apiVersion: kagent.dev/v1alpha2
  kind: Agent
  metadata:
    name: opsman
    namespace: kagent
  spec:
    type: BYO
    description: Operates the cluster from inside it (ADR-0001).
    byo:
      deployment:
        image: ghcr.io/vladtara/abox/opsman:<tag>
        env:
          - name: EMBEDDINGS_BASE_URL
            value: http://127.0.0.1:8090
        volumes:
          # The GGUF, from the same image llm-d mounts (ADR-0002). An image
          # volume mounts that image's rootfs, so the file lands at
          # <mountPath>/models/model.gguf.
          - name: nomic-model
            image:
              reference: ghcr.io/den-vasyliev/abox/nomic-embed@sha256:74be142b7496cdf2ca0dbf9bd64c98648e1a64147795b61e569168cc4dd439fd
              pullPolicy: IfNotPresent
        extraContainers:
          - name: embedder
            # Same build as llm-d's decode container, so the vectors match.
            image: ghcr.io/ggml-org/llama.cpp:server-b10920
            command: ["/app/llama-server"]
            args:
              - --host
              - 127.0.0.1
              - --port
              - "8090"
              - --embeddings
              - --ctx-size
              - "4096"
              - --ubatch-size
              - "2048"
              - --parallel
              - "2"
              - --model
              - /model-cache/models/model.gguf
            volumeMounts:
              - name: nomic-model
                mountPath: /model-cache
                readOnly: true
            # exec, not httpGet: the server listens on 127.0.0.1 and the
            # kubelet's httpGet would call the pod IP.
            startupProbe:
              exec:
                command: [curl, -fsS, http://127.0.0.1:8090/health]
              periodSeconds: 5
              failureThreshold: 12
            readinessProbe:
              exec:
                command: [curl, -fsS, http://127.0.0.1:8090/health]
              periodSeconds: 10
            livenessProbe:
              exec:
                command: [curl, -fsS, http://127.0.0.1:8090/health]
              periodSeconds: 30
            resources:
              requests:
                cpu: 250m
                memory: 256Mi
              limits:
                cpu: "2"
                memory: 1Gi
  ```

- [ ] opsman retries `GET /health` on `127.0.0.1:8090` until it returns 200
  before its first embed. BYO agents have no init containers, so nothing else
  orders startup.
- [ ] `kubectl kustomize releases` renders without errors.
- [ ] PR, merge, `make push` (tag), then wait for Flux:
  `kubectl -n flux-system get kustomization releases` shows `Ready True` at the
  new revision.

### A.2 Verify

- [ ] The Agent is ready and the pod has both containers:

  ```sh
  kubectl -n kagent get agent opsman
  kubectl -n kagent get deploy opsman -o jsonpath='{.spec.template.spec.containers[*].name}'
  ```

  Expected: `READY True`; the container list includes `embedder`.

- [ ] The sidecar serves the pinned model:

  ```sh
  kubectl -n kagent exec deploy/opsman -c embedder -- curl -s 127.0.0.1:8090/v1/models \
    | jq '.data[0].meta | {n_embd, n_ctx_train, n_params, ftype}'
  ```

  Expected: 768, 2048, 136727040, "Q8_0"

- [ ] It answers an embedding call:

  ```sh
  kubectl -n kagent exec deploy/opsman -c embedder -- curl -s 127.0.0.1:8090/v1/embeddings \
    -H 'content-type: application/json' -d '{"input":"search_query: which pods are not ready?"}' \
    | jq '{dims: (.data[0].embedding | length), first: [.data[0].embedding[:3][] | . * 100000 | round / 100000]}'
  ```

  Expected: `dims` 768, `first` close to `[-0.04349, 0.02644, -0.17724]`.

- [ ] Nothing outside the pod can reach it:

  ```sh
  POD_IP=$(kubectl -n kagent get pod -l app.kubernetes.io/name=opsman -o jsonpath='{.items[0].status.podIP}')
  kubectl -n kagent run embed-reach --rm -i --restart=Never --image=curlimages/curl:8.16.0 -- \
    curl -sS -m 3 "http://$POD_IP:8090/health"
  ```

  Expected: `Connection refused` (or a timeout). A `{"status":"ok"}` means it
  is bound to the pod IP; fix `--host`.

- [ ] It is the same vector space as llm-d (run
  [the comparison](#same-space-check) with `A=18090`).
- [ ] Record the sidecar's memory in ADR-0003, "Consequences". There is no
  metrics-server, so read the kubelet:

  ```sh
  NODE=$(kubectl -n kagent get pod -l app.kubernetes.io/name=opsman -o jsonpath='{.items[0].spec.nodeName}')
  kubectl get --raw "/api/v1/nodes/$NODE/proxy/stats/summary" \
    | jq '.pods[] | select(.podRef.name | startswith("opsman")) | .containers[] | select(.name == "embedder") | .memory | {rss_mib: (.rssBytes / 1048576), ws_mib: (.workingSetBytes / 1048576)}'
  ```

### A.3 Roll back

- [ ] Remove the `embedder` container and the `nomic-model` volume, and set
  `EMBEDDINGS_BASE_URL` to `http://llm-d-embedding.llm-d:8000` (Part B). Same
  GitOps path.

## Part B: llm-d route

`releases/llmd.yaml` already runs it. Embeddings go to the decode Service, not
the InferencePool; the EPP's scorers are built for generation.

**Done when** every box in B.1 is checked.

### B.1 Verify

- [ ] Both releases are ready and the pool exists:

  ```sh
  kubectl -n llm-d get helmrelease llm-d-embedding llm-d-pool
  kubectl -n llm-d get inferencepool llm-d-pool
  kubectl -n llm-d get pods
  ```

  Expected: both `Ready True`; the decode pod and `llm-d-pool-epp` are
  `Running`.

- [ ] The decode Service serves the pinned model and embeds:

  ```sh
  kubectl -n llm-d port-forward svc/llm-d-embedding 18000:8000 >/dev/null &
  curl -s 127.0.0.1:18000/v1/models | jq '.data[0].meta | {n_embd, ftype}'
  curl -s 127.0.0.1:18000/v1/embeddings -H 'content-type: application/json' \
    -d '{"input":"search_query: which pods are not ready?"}' | jq '.data[0].embedding | length'
  ```

  Expected: `n_embd` 768, `ftype` "Q8_0", then 768.

- [ ] It is reachable through the gateway:

  ```sh
  GW=$(kubectl -n agentgateway-system get gateway agentgateway-external -o jsonpath='{.status.addresses[0].value}')
  curl -s "http://$GW/llmd/v1/embeddings" -H 'content-type: application/json' \
    -d '{"input":"search_query: which pods are not ready?"}' | jq '.data[0].embedding | length'
  ```

  Expected: 768. On a Codespace the gateway address (for example
  `172.18.0.5`) is only reachable from inside the Codespace.

### B.2 Move opsman to llm-d

Only when one of these holds: opsman runs more than one replica, a second
consumer needs the same embeddings, or the model changes to one llama.cpp
cannot serve.

- [ ] Run [the comparison](#same-space-check) between the sidecar and llm-d.
  All pairs at 0.999 or above means no re-embed; anything lower means a new
  collection (ADR-0002, "one collection, one model").
- [ ] Set `EMBEDDINGS_BASE_URL` to `http://llm-d-embedding.llm-d:8000`, and
  remove the `embedder` container and the `nomic-model` volume. Same GitOps
  path as A.1.
- [ ] Re-run the embed check from opsman's side and confirm vector search
  still finds a known entry.

## Same-space check

Compares two routes over the same texts. Start both port-forwards first:

```sh
kubectl -n kagent port-forward deploy/opsman 18090:8090 >/dev/null &   # sidecar (Part A)
kubectl -n llm-d port-forward svc/llm-d-embedding 18000:8000 >/dev/null &
```

`kubectl port-forward` reaches ports bound to `127.0.0.1` inside the pod, so
the sidecar is reachable this way even though other pods cannot call it.

```sh
A=18090 B=18000 python3 - <<'EOF'
import json, os, urllib.request
texts = [
    "search_query: which pods are not ready?",
    "search_document: apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: qdrant-mcp\n  namespace: kagent",
    "search_document: Back-off restarting failed container embedder in pod opsman",
    "search_query: what memory limit does the phoenix pod have?",
    "search_document: HelmRelease llm-d-pool depends on llm-d-embedding in namespace llm-d",
]
def emb(port):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/embeddings",
        data=json.dumps({"input": texts}).encode(), headers={"content-type": "application/json"})
    data = sorted(json.load(urllib.request.urlopen(req))["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]
a, b = emb(os.environ["A"]), emb(os.environ["B"])
sims = [sum(x * y for x, y in zip(u, v)) for u, v in zip(a, b)]
print(" ".join(f"{s:.5f}" for s in sims))
assert min(sims) >= 0.999, "different vector spaces: re-embed before switching"
EOF
```

Expected: five values at 0.999 or above, no assertion error.

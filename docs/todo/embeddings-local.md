# ToDo: run nomic-embed-text-v1.5 locally with llama.cpp

For the agent working on this repo. The decision behind it is
[ADR-0002](../adr/0002-embedding-model.md).

**Done when** every box under "Verify" is checked and
`http://127.0.0.1:8090/v1/embeddings` returns a 768-dimension vector.

Rules while doing it:

- Stop at the first check that fails and report its output; do not work
  around it.
- Publish the port on `127.0.0.1` only.
- Never commit the model file.

This uses the same llama.cpp build (b10920) and the same Q8_0 file as llm-d
and the opsman sidecar, so the vectors match the cluster's.

## Prerequisites

- [ ] `docker version` answers (both client and server)
- [ ] `jq` and `python3` are on `PATH`
- [ ] about 200 MB free disk
- [ ] port 8090 is free: `ss -ltn '( sport = :8090 )'` shows only the header

## Get the model

- [ ] Download the Q8_0 GGUF:

  ```sh
  mkdir -p ~/models
  curl -fL --retry 3 -o ~/models/nomic-embed-text-v1.5.Q8_0.gguf \
    https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q8_0.gguf
  ```

- [ ] Check it is the pinned file (146,146,432 bytes):

  ```sh
  echo "3e24342164b3d94991ba9692fdc0dd08e3fd7362e0aacc396a9a5c54a544c3b7  $HOME/models/nomic-embed-text-v1.5.Q8_0.gguf" | sha256sum -c
  ```

  Expected: `...Q8_0.gguf: OK`

## Run it

- [ ] Start the server. The image's entrypoint is `/app/llama-server` and its
  Docker health check calls port 8080, so the server listens on 8080 inside
  and is published as 8090:

  ```sh
  docker run -d --name nomic-embed \
    -p 127.0.0.1:8090:8080 \
    -v "$HOME/models:/models:ro" \
    ghcr.io/ggml-org/llama.cpp:server-b10920 \
    --host 0.0.0.0 --port 8080 --embeddings \
    --ctx-size 4096 --ubatch-size 2048 --parallel 2 \
    --model /models/nomic-embed-text-v1.5.Q8_0.gguf
  ```

  `--embeddings` turns on `/v1/embeddings`. `--ctx-size 4096` over
  `--parallel 2` gives two slots of 2048 tokens, the model's window.
  `--ubatch-size` must be at least a slot, because a pooled embedding has to
  fit in one micro-batch.

- [ ] Wait until it is ready (503 while loading):

  ```sh
  until curl -fsS 127.0.0.1:8090/health; do sleep 1; done
  ```

  Expected: `{"status":"ok"}`

## Verify

- [ ] It loaded the right model:

  ```sh
  curl -s 127.0.0.1:8090/v1/models | jq '.data[0].meta | {n_embd, n_ctx_train, n_params, ftype}'
  ```

  Expected: `n_embd` 768, `n_ctx_train` 2048, `n_params` 136727040, `ftype` "Q8_0"

- [ ] It answers an embedding call, with the query prefix:

  ```sh
  curl -s 127.0.0.1:8090/v1/embeddings -H 'content-type: application/json' \
    -d '{"input":"search_query: which pods are not ready?"}' \
    | jq '{dims: (.data[0].embedding | length), first: [.data[0].embedding[:3][] | . * 100000 | round / 100000]}'
  ```

  Expected: `dims` 768. `first` close to `[-0.04349, 0.02644, -0.17724]`, the
  cluster's answer on 2026-09-30. A different CPU can move the last digit; a
  different sign or magnitude means a different model or quant.

- [ ] Retrieval works with prefixes: a query scores its matching document above
  an unrelated one.

  ```sh
  python3 - <<'EOF'
  import json, urllib.request
  def emb(texts):
      req = urllib.request.Request("http://127.0.0.1:8090/v1/embeddings",
          data=json.dumps({"input": texts}).encode(), headers={"content-type": "application/json"})
      data = sorted(json.load(urllib.request.urlopen(req))["data"], key=lambda d: d["index"])
      return [d["embedding"] for d in data]
  q, hit, miss = emb([
      "search_query: why is my pod stuck in CrashLoopBackOff?",
      "search_document: CrashLoopBackOff means a container keeps exiting and the kubelet restarts it with a growing back-off.",
      "search_document: Qdrant stores vectors in collections and searches them by similarity.",
  ])
  cos = lambda a, b: sum(x * y for x, y in zip(a, b))  # /v1/embeddings returns unit vectors
  print(f"hit {cos(q, hit):.3f}  miss {cos(q, miss):.3f}")
  assert cos(q, hit) > cos(q, miss), "prefix or model problem"
  EOF
  ```

  Expected: `hit` clearly above `miss`, no assertion error.

- [ ] Record the results (the three outputs above, `docker stats --no-stream nomic-embed`)
  in the PR or run notes.

## Clean up

- [ ] `docker rm -f nomic-embed` once done. Keep `~/models` for the next run.

## If something fails

| Symptom | Cause | Fix |
|---|---|---|
| `sha256sum: WARNING: 1 computed checksum did NOT match` | partial or different file | delete it and download again |
| `/health` keeps returning 503 | model still loading, or wrong path | `docker logs nomic-embed`; check the `--model` path |
| `/v1/embeddings` returns 501 | started without `--embeddings` | recreate the container with the flag |
| error that the input is too large for the batch size | input longer than 2048 tokens | chunk the text; do not raise the window (ADR-0002) |
| `/v1/chat/completions` returns an error | expected, this is an embedding model | none |

## Fallback: Ollama

Only if Docker is not an option.

```sh
ollama pull nomic-embed-text:v1.5
curl -s localhost:11434/api/embed \
  -d '{"model":"nomic-embed-text:v1.5","input":"search_query: which pods are not ready?"}' \
  | jq '.embeddings[0] | length'
```

Expected: 768. Ollama ships this model as f16, not Q8_0, so its vectors are a
different space. Use it to try things out; never write its vectors to a
collection the cluster reads.

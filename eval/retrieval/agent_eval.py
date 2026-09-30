"""Ask retrieval-agent every question over kagent's A2A API and score the runs.

  python3 agent_eval.py questions.json out.json LABEL

Standard library only, so it runs in any Python 3 pod. questions.json is
questions.yaml converted to JSON. Each question is a fresh A2A task: no
context is carried between questions.

Per question it records the final answer, every tool call the agent made,
whether any vector-store call returned an expected object, and whether the
answer contains every fact group in the question's `facts`.
"""

import json
import re
import statistics
import sys
import time
import urllib.request
import uuid

URL = "http://kagent-controller.kagent:8083/api/a2a/kagent/retrieval-agent/"
FIND = {"qdrant-find", "vector_find"}
GRAPH = {"get-schema", "read-cypher", "write-cypher"}


def ask(text):
    body = {"jsonrpc": "2.0", "id": 1, "method": "message/send",
            "params": {"message": {"role": "user", "messageId": str(uuid.uuid4()),
                                   "parts": [{"kind": "text", "text": text}]}}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.monotonic()
    r = json.load(urllib.request.urlopen(req, timeout=300))
    return r, time.monotonic() - t0


def ids_in(blob):
    """"<key>#<kind>" pairs in a tool response, however deeply it is escaped.

    Both servers write the metadata we stored, and in both the "key" field is
    followed by its "kind" (insertion order in one, sorted keys in the other).
    """
    s = json.dumps(blob)
    keys = [(m.start(), m.group(1)) for m in re.finditer(r'\\*"key\\*"\s*:\s*\\*"([^"\\]+)', s)]
    kinds = [(m.start(), m.group(1)) for m in re.finditer(r'\\*"kind\\*"\s*:\s*\\*"([^"\\]+)', s)]
    out = []
    for pos, key in keys:
        kind = next((k for p, k in kinds if p > pos), None)
        out.append(f"{key}#{kind}")
    return list(dict.fromkeys(out))


def matches(answer, groups):
    a = answer.lower()
    return all(any(str(alt).lower() in a for alt in g) for g in groups)


def main(qpath, out_path, label):
    qs = json.load(open(qpath))["questions"]
    rows = []
    for q in qs:
        try:
            r, dt = ask(q["query"])
            res = r.get("result") or {}
            err = r.get("error")
        except Exception as e:  # a timeout or a 5xx is a failed run, not a crash
            res, dt, err = {}, 0.0, str(e)
        calls, retrieved = [], []
        for m in res.get("history", []):
            for p in m.get("parts", []):
                if p.get("kind") != "data":
                    continue
                kind = (p.get("metadata") or {}).get("adk_type")
                d = p.get("data") or {}
                if kind == "function_call":
                    calls.append({"name": d.get("name"), "args": d.get("args")})
                elif kind == "function_response" and d.get("name") in FIND:
                    retrieved += ids_in(d.get("response"))
        answer = " ".join(p.get("text", "") for a in res.get("artifacts", [])
                          for p in a.get("parts", []) if p.get("kind") == "text").strip()
        usage = (res.get("metadata") or {}).get("adk_usage_metadata") or {}
        row = {
            "id": q["id"], "type": q["type"], "query": q["query"], "expect": q["expect"],
            "state": (res.get("status") or {}).get("state"), "error": err,
            "seconds": round(dt, 1), "answer": answer, "calls": calls,
            "find_calls": sum(c["name"] in FIND for c in calls),
            "graph_calls": sum(c["name"] in GRAPH for c in calls),
            "retrieved": list(dict.fromkeys(retrieved)),
            "retrieved_expected": any(e in retrieved for e in q["expect"]),
            "facts_ok": matches(answer, q["facts"]),
            "prompt_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
        }
        rows.append(row)
        print(f"{label} {q['id']:<4} {q['type']:<7} facts={'Y' if row['facts_ok'] else 'n'} "
              f"retrieved={'Y' if row['retrieved_expected'] else '-'} find={row['find_calls']} "
              f"graph={row['graph_calls']} {dt:5.1f}s {row['state']}", flush=True)
        json.dump(rows, open(out_path, "w"), indent=1)

    scored = [r for r in rows if r["expect"]]
    print(f"\n{label}: answers with all facts {sum(r['facts_ok'] for r in rows)}/{len(rows)}, "
          f"expected object retrieved {sum(r['retrieved_expected'] for r in scored)}/{len(scored)}, "
          f"find calls mean {statistics.mean(r['find_calls'] for r in rows):.2f}, "
          f"graph calls mean {statistics.mean(r['graph_calls'] for r in rows):.2f}, "
          f"latency p50 {statistics.median(r['seconds'] for r in rows):.1f}s")


if __name__ == "__main__":
    main(*sys.argv[1:])

"""Print the report's tables from the saved runs and the hand grades.

  python3 eval/retrieval/summarize.py eval/retrieval/results
"""

import json
import statistics as st
import sys

import yaml

R = sys.argv[1]
qs = {q["id"]: q for q in yaml.safe_load(open("eval/retrieval/questions.yaml"))["questions"]}
grades = yaml.safe_load(open("eval/retrieval/grades.yaml"))
TYPES = ["shallow", "para", "why", "deep"]


def raw(path):
    rows = [r for r in json.load(open(path)) if r["expect"]]
    def m(sel):
        n = len(sel)
        return (sum(r["rank"] == 1 for r in sel) / n, sum(r["rank"] is not None for r in sel) / n,
                sum(1 / r["rank"] for r in sel if r["rank"]) / n, n)
    out = {"all": m(rows)}
    out.update({t: m([r for r in rows if r["type"] == t]) for t in TYPES})
    out["p50"] = st.median(r["ms"] for r in json.load(open(path)))
    return out


print("## Raw retrieval (no LLM)\n")
a, b = raw(f"{R}/raw-minilm.json"), raw(f"{R}/raw-nomic.json")
print("| Subset | n | Hit@1 MiniLM | Hit@1 nomic | Hit@5 MiniLM | Hit@5 nomic | MRR MiniLM | MRR nomic |")
print("|---|---|---|---|---|---|---|---|")
for k in ["all"] + TYPES:
    print(f"| {k} | {a[k][3]} | {a[k][0]:.2f} | {b[k][0]:.2f} | {a[k][1]:.2f} | {b[k][1]:.2f} | {a[k][2]:.3f} | {b[k][2]:.3f} |")
print(f"| search latency p50 | | {a['p50']:.0f} ms | {b['p50']:.0f} ms | | | | |")

print("\n## Agentic retrieval\n")
runs = [("A: official, v1.0.0 prompt", "agent-official-baseline.json", None),
        ("A2: official, v1.1.0 prompt", "agent-official.json", "official"),
        ("B: default, v1.2.0", "agent-default.json", "default")]
print("| Run | Keyword score | Hand: correct / partial / wrong | Hallucinations | Grade mean | "
      "Expected object retrieved | vector calls | graph calls | Latency p50 |")
print("|---|---|---|---|---|---|---|---|---|")
for label, f, g in runs:
    rows = json.load(open(f"{R}/{f}"))
    kw = sum(r["facts_ok"] for r in rows)
    ret = sum(r["retrieved_expected"] for r in rows if r["expect"])
    nret = sum(1 for r in rows if r["expect"])
    hand = "not graded"
    hall = mean = "-"
    if g:
        gs = [v for k, v in grades[g].items() if k not in grades["excluded"]]
        pts = [0 if v == "h" else v for v in gs]
        hand = f"{pts.count(2)} / {pts.count(1)} / {pts.count(0)} of {len(pts)}"
        hall = str(gs.count("h"))
        mean = f"{sum(pts) / (2 * len(pts)):.2f}"
    print(f"| {label} | {kw}/{len(rows)} | {hand} | {hall} | {mean} | {ret}/{nret} | "
          f"{st.mean(r['find_calls'] for r in rows):.2f} | {st.mean(r['graph_calls'] for r in rows):.2f} | "
          f"{st.median(r['seconds'] for r in rows):.1f} s |")

print("\n## Per question\n")
ra = {r["id"]: r for r in json.load(open(f"{R}/raw-minilm.json"))}
rb = {r["id"]: r for r in json.load(open(f"{R}/raw-nomic.json"))}
print("| Id | Type | Raw rank MiniLM | Raw rank nomic | Agent official | Agent default |")
print("|---|---|---|---|---|---|")
for qid, q in qs.items():
    go, gd = grades["official"].get(qid, "excl."), grades["default"].get(qid, "excl.")
    rank = lambda r: str(r[qid]["rank"]) if r[qid]["rank"] else ("n/a" if not q["expect"] else "miss")
    print(f"| {qid} | {q['type']} | {rank(ra)} | {rank(rb)} | {go} | {gd} |")

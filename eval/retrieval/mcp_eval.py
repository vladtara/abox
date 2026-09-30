# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp==1.24.0", "pyyaml==6.0.3"]
# ///
"""Drive the two Qdrant MCP servers directly, with no LLM in the loop.

  list    URL                         tools the server exposes
  ingest  URL STORE_TOOL corpus.json  store every document, same text and metadata
  search  URL FIND_TOOL questions.yaml out.json
                                      run each question's query, rank hits by key

Both servers are reached the way kagent reaches them: kmcp's streamable HTTP
adapter on :3000/mcp. Search results are reduced to the ordered list of
distinct metadata keys they contain, so a server that returns several chunks
of one manifest is not credited for them twice.
"""

import asyncio
import contextlib
import json
import re
import sys
import time

import yaml
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


@contextlib.asynccontextmanager
async def session(url):
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


def result_text(res):
    return "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")


def ids_from(res):
    """Ordered, distinct "<key>#<kind>" ids in a find result, for either server.

    Kind is part of the id because one namespace/name is often several objects:
    llm-d/llm-d-embedding is a HelmRelease, a Service and an HTTPRoute.
    """
    try:
        items = json.loads(result_text(res))
    except json.JSONDecodeError:
        return []
    found = []
    for item in items if isinstance(items, list) else []:
        md = None
        if isinstance(item, str):
            # mcp-server-qdrant: a header line, then
            # "<entry><content>..</content><metadata>{json}</metadata></entry>"
            m = re.search(r"<metadata>(.*?)</metadata>", item, re.S)
            md = json.loads(m.group(1)) if m else None
        elif isinstance(item, dict):
            # qdrant-mcp: {"payload": {...metadata, document, doc, chunk}, "score": ..}
            p = item.get("payload", {})
            md = p.get("metadata", p)
        if md and md.get("key"):
            found.append(f"{md['key']}#{md.get('kind')}")
    return list(dict.fromkeys(found))


async def cmd_list(url):
    async with session(url) as s:
        for t in (await s.list_tools()).tools:
            props = list((t.inputSchema or {}).get("properties", {}).keys())
            print(f"{t.name}({', '.join(props)}): {(t.description or '').splitlines()[0][:100]}")


async def cmd_ingest(url, tool, corpus_path):
    corpus = json.load(open(corpus_path))
    async with session(url) as s:
        t0 = time.monotonic()
        for i, d in enumerate(corpus["docs"], 1):
            meta = {k: d[k] for k in ("key", "kind", "name", "namespace", "file")}
            res = await s.call_tool(tool, {"information": d["text"], "metadata": meta})
            status = "ERROR " if res.isError else ""
            print(f"{i:>3}/{len(corpus['docs'])} {status}{d['kind']} {d['key']}: {result_text(res)[:90]}")
        print(f"ingested {len(corpus['docs'])} documents in {time.monotonic() - t0:.1f}s")


async def cmd_search(url, tool, questions_path, out_path):
    qs = yaml.safe_load(open(questions_path))["questions"]
    rows = []
    async with session(url) as s:
        for q in qs:
            args = {"query": q["query"]}
            if tool == "vector_find":
                args["limit"] = 5
            t0 = time.monotonic()
            res = await s.call_tool(tool, args)
            ms = (time.monotonic() - t0) * 1000
            ranked = ids_from(res)
            rank = next((i + 1 for i, k in enumerate(ranked) if k in q["expect"]), None)
            rows.append({"id": q["id"], "type": q["type"], "query": q["query"],
                         "expect": q["expect"], "ranked": ranked, "rank": rank,
                         "ms": round(ms, 1), "raw": result_text(res)[:4000]})
            print(f"{q['id']:<4} {q['type']:<5} rank={rank or '-':<2} {ms:6.0f}ms  top={ranked[:3]}")
    json.dump(rows, open(out_path, "w"), indent=1)
    # Questions with no expected object test abstention; they have no rank.
    scored = [r for r in rows if r["expect"]]
    for label, sel in [("all", scored)] + [
            (t, [r for r in scored if r["type"] == t]) for t in sorted({r["type"] for r in scored})]:
        n = len(sel)
        hit1 = sum(r["rank"] == 1 for r in sel) / n
        hit5 = sum(r["rank"] is not None for r in sel) / n
        mrr = sum(1 / r["rank"] for r in sel if r["rank"]) / n
        print(f"{label:<8} n={n:<3} hit@1={hit1:.2f} hit@5={hit5:.2f} mrr={mrr:.3f}")
    print(f"latency p50={sorted(r['ms'] for r in rows)[len(rows) // 2]:.0f}ms")


def main():
    cmd, *args = sys.argv[1:]
    fn = {"list": cmd_list, "ingest": cmd_ingest, "search": cmd_search}[cmd]
    asyncio.run(fn(*args))


if __name__ == "__main__":
    main()

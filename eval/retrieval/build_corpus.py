"""Split releases/ into one document per Kubernetes object: the corpus both
Qdrant MCP servers ingest, so the embedding model is the only thing that
differs between the two collections.

  python3 eval/retrieval/build_corpus.py > corpus.json

Comments are kept -- they carry the rationale most of the "why" questions ask
about. An object declared in two files (a Namespace in both a CRD and an app
file) is kept once, from the first file in sort order.
"""

import glob
import json
import re
import subprocess
import sys

import yaml


def main():
    docs, seen = [], set()
    for f in sorted(glob.glob("releases/**/*.yaml", recursive=True)):
        if f.endswith("kustomization.yaml"):
            continue
        for part in re.split(r"(?m)^---\s*$", open(f).read()):
            obj = yaml.safe_load(part) if part.strip() else None
            if not isinstance(obj, dict) or "kind" not in obj:
                continue
            md = obj.get("metadata", {})
            ns = md.get("namespace", "cluster")
            ident = (f"{ns}/{md['name']}", obj["kind"])
            if ident in seen:
                print(f"skip duplicate {ident[1]} {ident[0]} in {f}", file=sys.stderr)
                continue
            seen.add(ident)
            docs.append({"key": ident[0], "kind": obj["kind"], "name": md["name"],
                         "namespace": ns, "file": f, "text": part.strip() + "\n"})
    commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    json.dump({"commit": commit, "docs": docs}, sys.stdout, indent=1)


if __name__ == "__main__":
    main()

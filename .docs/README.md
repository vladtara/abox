# abox scout report

A read-through of the abox repo: how it works today, and what is broken, risky, or out of date.

abox turns `make run` into a local KinD cluster running an AI stack (agentgateway, kagent, Qdrant, Arize Phoenix, agentregistry). OpenTofu creates the cluster and bootstraps the Flux Operator. After that, Flux reconciles everything from an OCI artifact of `releases/` that CI publishes on every `v*` tag. There is no application code.

## Snapshot

| | |
|---|---|
| Date | 2026-09-25 |
| Commit | `dd31e8a` on `main`: `upstream/main` (`390671c`) plus one local commit that adds `.docs` to `.gitignore` |
| Remotes | `origin` = `vladtara/abox` (fork, no tags); `upstream` = `den-vasyliev/abox` |
| Tags | 134 local, fetched from upstream. Highest overall: `v0.11.33`. Highest on `main`: `v0.6.5` = `4bbe4d3` |
| Size | 28 tracked files, 2,103 lines |

Every `path:line` reference is valid at both `390671c` and `dd31e8a`.

The scout started at `4bbe4d3`. During the session, local `main` moved twice:

1. It advanced to `390671c` (upstream commits `ccdd528` and `390671c`), and the upstream tags were fetched. Those commits change only `.github/workflows/flux-push.yaml`, `bootstrap/flux.tf`, and `bootstrap/variables.tf`. `releases/`, `scripts/`, and the `Makefile` are identical to `4bbe4d3` (**verified** with `git diff --quiet`).
2. `dd31e8a` added `.docs` to the end of `.gitignore`, so this folder is untracked and no cited line moved.

## Method

Each claim in these docs carries one of two tags:

- **verified**: backed by a command run during the scout
- **read**: from reading the source, commit history, or upstream specs

Commands run:

| Command | Result |
|---|---|
| `kubectl kustomize releases/crds` | renders: 2 Namespace, 2 OCIRepository, 2 HelmRelease, 1 GitRepository, 1 Kustomization |
| `kubectl kustomize releases` | renders: 5 Namespace, 4 OCIRepository, 1 HelmRepository, 5 HelmRelease, Gateway, HTTPRoute, ReferenceGrant |
| `make -n push`, with 0 local tags (session start) | `git tag v..1` |
| `make -n push`, with 134 local tags (now) | `git tag v0.11.34` |
| `git ls-remote --tags` on both remotes | `origin`: none. `upstream`: up to `v0.11.36`; `v0.6.5` → `4bbe4d3` |
| scratch Makefile | confirms GNU make expands the whole recipe before running its first line |

Nothing was tagged, pushed, or applied. These weren't possible here: no kind cluster was running and `tofu` isn't installed. So there was no live `tofu apply`, no Flux reconcile, and no image pulls.

## Top findings

1. [`make push` derives the version from the wrong tags](findings.md#1-make-push-derives-the-version-from-the-wrong-tags). Today it would tag `main` as `v0.11.34`, a version upstream already has (verified)
2. [Releases are frozen at 0.6.5](findings.md#3-releases-are-frozen-at-065). The RSIP filter is hard-pinned, so nothing released from `main` reaches a cluster until someone widens it
3. [Releases from this fork never reach the cluster](findings.md#4-fork-and-registry-mismatch): CI publishes under `vladtara`, but the cluster polls `den-vasyliev`
4. [Two Flux Kustomizations own the same two Namespaces](findings.md#6-two-kustomizations-own-the-same-namespaces), both with `prune: true` (verified)
5. The review and eval harness enforce stale or incorrect rules: the [kagent 0.7.23 pin](findings.md#11-kagent-0723-pin-is-stale) and the [ReferenceGrant rule](findings.md#12-referencegrant-rule-targets-the-wrong-case)

## Files

- [architecture.md](architecture.md): bootstrap chain, reconcile chain, artifact streams, component inventory, traffic path, and the reasons behind each workaround
- [workflows.md](workflows.md): prerequisites, `make` targets, `setup.sh`, the release pipeline, CI, and the review and eval harness
- [findings.md](findings.md): 19 ranked findings, each with evidence and a suggested direction

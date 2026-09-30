# Findings

Ranked findings from the scout of commit `390671c`. Back to [index](README.md).

Each finding is tagged **verified** (command run) or **read** (source, history, or upstream spec). "Direction" is a suggestion only; nothing here has been changed.

| # | Finding | Type | Tag |
|---|---|---|---|
| 1 | `make push` derives the version from the wrong tags | bug | verified |
| 2 | `make push` rolls the patch past 9 | bug | verified |
| 3 | Releases are frozen at 0.6.5 | bug (intentional hold) | verified |
| 4 | Fork and registry mismatch | bug | read |
| 5 | kagent OpenAI key is a placeholder | bug | read |
| 6 | Two Kustomizations own the same Namespaces | risk | verified |
| 7 | No PR validation for `releases/` | risk | read |
| 8 | CI hygiene | risk | read |
| 9 | Floating versions outside HelmReleases | risk | read |
| 10 | `setup.sh` rough edges | risk | read |
| 11 | kagent 0.7.23 pin is stale | doc drift | read |
| 12 | ReferenceGrant rule targets the wrong case | doc drift | read |
| 13 | gateway-api CRDs are no longer a HelmRelease | doc drift | read |
| 14 | Release docs predate the pin and per-branch streams | doc drift | verified |
| 15 | Component inventory is incomplete | doc drift | read |
| 16 | Smaller drift in CONTRIBUTING and README | doc drift | read |
| 17 | Phoenix, qdrant, and agentregistry have no route | question | read |
| 18 | agentregistry chart and image versions differ | question | read |
| 19 | qdrant is the only HelmRepository source | question | read |

---

## Confirmed bugs

### 1. make push derives the version from the wrong tags

**Verified.** `Makefile:36-41`:

```make
@git fetch origin --tags --force
$(eval TAG=$(shell git tag --list 'v*' | sort -V | tail -1 | sed 's/^v//' || echo "0.0.0"))
...
$(eval NEW_TAG=v$(MAJOR).$(MINOR).$(shell echo $$(($(PATCH)+1))))
```

This target has three separate problems.

**a) It uses the highest tag on any branch, not the highest on `main`.** With the upstream tags fetched, it resolves to `v0.11.33`. That tag lives only on `upstream/feat/otel-demo` and `upstream/feat/triage`, while the highest tag in `main`'s history is `v0.6.5`:

```
$ make -n push
git fetch origin --tags --force
git tag v0.11.34
git push origin main v0.11.34
```

Upstream already has `v0.11.34`, pointing at another commit (`git ls-remote --tags`), and goes up to `v0.11.36`. Run against upstream, the push would be rejected; run against this fork, it would succeed and release `main` as `0.11.34`. Commit `390671c` split the artifact streams per branch, but this version calculation still merges every branch's tags.

**b) The fetch runs too late, and from the wrong remote.** GNU make expands every line of a recipe before running the first one, so the `$(eval ...)` lines read local tags before `git fetch` runs (verified with a scratch Makefile). The fetch also targets `origin`, the fork, which has no tags. It never contributes anything.

**c) The empty-tag fallback is dead code.** With no `v*` tags, the pipeline prints nothing and exits 0, so `|| echo "0.0.0"` never runs. At the start of the session, before the tags were fetched, `make -n push` printed `git tag v..1`. A fresh clone of this fork (0 tags on `origin`) would hit the same thing.

**Direction.** Compute the version in the shell, after the fetch, from `main`'s own history. For example, use `git describe --tags --abbrev=0 --match 'v[0-9]*' main`, or `git tag --merged main --list 'v*' | sort -V | tail -1`, then default with `${TAG:-0.0.0}`. Fetch from the remote the tag will be pushed to.

### 2. make push rolls the patch past 9

**Verified.** From `v0.3.9`, `Makefile:41` produces `v0.3.10`. This has already happened 34 times: that many local tags have a patch number above 9 (for example `v0.11.33`). On the current tag set, `sort` ranks `v0.9.9` highest and `sort -V` ranks `v0.11.33` highest.

The docs forbid exactly this:

| Doc | Says |
|---|---|
| `CODEBASE.md:131` | forbidden pattern |
| `REVIEW.md:70,92,128` | "the `make push` logic must bump minor" |
| `README.md:57` | asks you to bump the minor by hand |

The Makefile has no rollover, so the claim in REVIEW.md is false. Whether the RSIP really sorts lexicographically comes from those docs and wasn't tested here. The 34 existing tags suggest either that it doesn't, or that those releases were never tracked by an open filter.

**Direction.** There are two options:

- Add the rollover to the Makefile (`PATCH == 9` → `MINOR+1.0`).
- Make the RSIP sort by semver. Check whether flux-operator 0.59.0 supports `filter.semver` on `OCIArtifactTag`. If it does, this constraint and the rules in four docs can be deleted.

Settle this before widening the filter in finding 3.

### 3. Releases are frozen at 0.6.5

**Verified** for the facts below; the effects are **read**.

- The RSIP filter is `includeTag: "^0\\.6\\.5$"` (`bootstrap/flux.tf:52`). Commit `ccdd528` pinned it there as a temporary hold (comment at `flux.tf:43-51`).
- Upstream `v0.6.5` points at `4bbe4d3`.
- `releases/` hasn't changed between `4bbe4d3` and `390671c`, so a cluster bootstrapped from `main` today does run `main`'s current manifests.

Effects:

- **Nothing released from here on reaches a default cluster.** `make push` tags, CI publishes, and the RSIP ignores it. The hold is silent: CI succeeds and the cluster simply stays on 0.6.5.
- **The pin is hard-coded, not a variable.** A feature-branch cluster that sets `releases_artifact = "releases-<branch>"` (the path `390671c` added) still accepts only `0.6.5`, which won't exist in its stream. The per-branch split can't be used as designed until the filter can be set per cluster.
- **Widening is blocked on cleanup.** The comment says to widen once tags 0.6.6 to 0.8.9 are gone from the `releases` repository. Given finding 1, `main`'s next `make push` would be `v0.11.x`, not `v0.6.6`.

**Direction.** Move the filter into a variable, for example `releases_tag_filter`, with the pin as its default for now. Before widening, fix finding 1 and settle finding 2. Track the registry cleanup somewhere visible, since the only reminder today is a code comment.

### 4. Fork and registry mismatch

**Read.** The fork and its cluster point at different registries:

| Side | Setting | Where |
|---|---|---|
| origin | `vladtara/abox` | git remote (`upstream` is `den-vasyliev/abox`) |
| CI publishes to | `oci://ghcr.io/${{ github.repository }}/<stream>`, which on the fork is `ghcr.io/vladtara/abox/releases` or `.../releases-<branch>` | `.github/workflows/flux-push.yaml:59` |
| Cluster polls | `var.oci_registry` = `oci://ghcr.io/den-vasyliev/abox` + `var.releases_artifact` | `bootstrap/variables.tf:10`, `bootstrap/flux.tf:42,83` |

A cluster built with `make run` tracks upstream's artifact. Tags pushed from this fork are published but never pulled. Also:

- The fallback tag `releases_version = "0.1.0"` (`bootstrap/variables.tf:27`) must exist in whichever repository is polled.
- Pulls are anonymous (no `secretRef`), so the fork's GHCR package must be public.
- `agentregistry` pulls its chart from `oci://ghcr.io/den-vasyliev/charts/agentregistry` (`releases/agentregistry.yaml:13`). That's a runtime dependency on the upstream owner's personal registry.

**Direction.** Override `oci_registry` for the fork. `*.tfvars` files are gitignored, so either change the default or pass `-var` from `setup.sh`. Also document the GHCR visibility requirement in the README.

### 5. kagent OpenAI key is a placeholder

**Read.** `releases/kagent.yaml:87-90`:

```yaml
providers:
  default: openAI
  openAI:
    apiKey: OPENAI_API_KEY
```

That value is the literal string, not a reference to an environment variable. Commit `10c19b1` removed the manual `kagent-openai` Secret creation from `setup.sh`, and neither the README nor CONTRIBUTING says how to supply a real key. As shipped, agents that call the default provider can't authenticate. A Secret patched by hand may be overwritten on the next Helm upgrade.

**Direction.** Document the manual step. Or have `setup.sh` create a Secret from `$OPENAI_API_KEY` and point the chart at it, if kagent 0.10.1 supports referencing an existing Secret (check the chart values).

---

## Risks

### 6. Two Kustomizations own the same Namespaces

**Verified** by rendering both kustomizations:

| Namespace | In `releases-crds` | In `releases` |
|---|---|---|
| `agentgateway-system` | `releases/crds/agentgateway-crds.yaml:1-4` | `releases/agentgateway.yaml:1-4` |
| `kagent` | `releases/crds/kagent-crds.yaml:1-4` | `releases/kagent.yaml:1-4` |

Both Kustomizations have `prune: true` (`bootstrap/flux.tf:97,112`). Each one records the Namespace in its inventory and stamps it with its own `kustomize.toolkit.fluxcd.io/*` ownership labels, so ownership flips to whichever applied last. If the Namespace is later removed from one file, whether that Kustomization prunes it depends on who applied last. Deleting a Namespace deletes everything in it (**read**, from Flux GC semantics).

How it got this way:

- `CODEBASE.md:108` says to define the Namespace next to its HelmRelease.
- Commit `9f7ffd8` moved `agentgateway-system` into `crds/` "so it exists before HelmRelease".
- The app-side copies were kept.

The doc rule behind this is also shaky. `CODEBASE.md:130`, `REVIEW.md:58,85,122`, and EVALS Example C call "namespace defined only in `releases/crds/`, used in `releases/`" a `[critical]` failure. But `releases` has `dependsOn: releases-crds` (`flux.tf:106-107`), and `releases-crds` uses `wait: true` (`flux.tf:98`), so a Namespace created by `releases-crds` already exists when `releases` applies (**read**). The case that actually breaks is the reverse: a Namespace defined only in `releases/` but needed by a HelmRelease in `releases/crds/`.

**Direction.** Give each Namespace a single owner. `releases-crds` has to be the owner, since it runs first and its HelmReleases live there. Remove the Namespace documents from `releases/agentgateway.yaml` and `releases/kagent.yaml`. Then rewrite the rule as "a Namespace must be created by the same Kustomization or one earlier in the `dependsOn` chain", and update Example C in EVALS.md.

### 7. No PR validation for releases/

**Read.** The only PR workflow triggers on `**.tf` and `**.tfvars` (`.github/workflows/terraform-pr-check.yml:8-10`). `flux-push.yaml` runs only on tags. Nothing renders or schema-checks `releases/` before it's published, and publishing also moves `latest` (`flux-push.yaml:67-71`). The only guard is the manual `flux get all` in `CONTRIBUTING.md:57`. The same gap covers `flux-push.yaml` itself: its new branch-resolution step (finding 8) had no PR check.

Both kustomizations render cleanly today (**verified**), so a check would be cheap to add.

**Direction.** Add a PR workflow on `releases/**` that runs `kubectl kustomize` (or `flux build kustomization`) on both paths, then kubeconform with Flux and Gateway API CRD schemas. Add `actionlint` for `.github/workflows/**`.

### 8. CI hygiene

**Read.**

- **Branch resolution edge cases** (`flux-push.yaml:37-53`, added in `390671c`):
  - If the tagged commit is on no remote branch, `branch` is empty and the repository becomes `releases-`. A trailing `-` isn't a valid OCI repository name, so the push should fail. This is the normal outcome of `make push` on an unpushed feature branch, because the target tags HEAD but pushes only `main` and the tag.
  - Only the last path segment of the branch is kept (`sed 's|^.*/||'`), so `feat/x` and `fix/x` share `releases-x`.
  - A commit that is on `main` and also on a feature branch goes to `releases`, which is the intended behaviour.
- **Terraform vs OpenTofu.** `terraform-pr-check.yml` uses the `dflook/terraform-*` actions, but the project requires OpenTofu (`Makefile:27-33`, `bootstrap/providers.tf:2-4`). The lock file only has `registry.opentofu.org` entries (`bootstrap/.terraform.lock.hcl:4,40,77,114`), so CI resolves providers again from `registry.terraform.io` and the locked versions don't apply. dflook publishes `tofu-*` equivalents.
- **Validate path.** `dflook/terraform-validate` runs without `path` (`terraform-pr-check.yml:25`), so it validates the repo root, which has no `.tf` files. Only `plan` targets `bootstrap/`.
- **Checkov.** Scoped to `check: CKV_AWS_*` (`terraform-pr-check.yml:49`) in a repo with no AWS resources, so it effectively checks nothing.
- **Unpinned actions and tools:**
  - `fluxcd/flux2/action@main` (`flux-push.yaml:22`)
  - `bridgecrewio/checkov-action@master` (`terraform-pr-check.yml:42`)
  - git-secrets cloned from HEAD (`terraform-pr-check.yml:60`)

### 9. Floating versions outside HelmReleases

**Read.** The "explicit tags only" rule (`CODEBASE.md:114`) covers HelmRelease `ref.tag`. Everything else floats:

| What | Where |
|---|---|
| FluxInstance `version: "2.x"` and `flux-operator-manifests:latest` | `bootstrap/flux-instance.yaml:14,16` |
| OpenTofu install script (latest) | `scripts/setup.sh:30`, `Makefile:15` |
| k9s through webi (latest) | `scripts/setup.sh:49`, `Makefile:16` |
| Provider constraints are all `>=` (the lock file pins them locally only) | `bootstrap/providers.tf:9-21` |
| CI moves the `latest` artifact tag on every release (the cluster ignores it) | `.github/workflows/flux-push.yaml:71` |

Two different `make run` invocations can end up with different Flux controller versions. Already pinned: kind v0.33.0, cloud-provider-kind 0.11.1, flux-operator chart 0.59.0, bootstrap module 0.8.0, and every chart.

### 10. setup.sh rough edges

**Read.**

- `cat >> ~/.bashrc` (`scripts/setup.sh:53-59`) runs on every invocation and stacks up duplicate alias blocks. It writes only to bash; zsh users, the macOS default, get nothing.
- `export KUBECONFIG=~/.kube/config` (line 81) affects only the rest of the script, which never uses kubectl.
- cloud-provider-kind runs from `/tmp` under `nohup` (lines 90-93). It stops on reboot, no target restarts it, and `make down` doesn't stop it. Without it, the Gateway Service gets no external IP.
- There's no wait for Flux readiness, although `README.md:25` says it "reconciles all components. When it finishes: ...".
- It never checks for Docker or kubectl, both of which are required.
- It still uses the legacy name `k8sdiy-env` (lines 10, 55).

---

## Doc drift

### 11. kagent 0.7.23 pin is stale

**Read.** The docs still describe the old kagent pin:

| Doc | Says |
|---|---|
| `CODEBASE.md:143` | "kagent pinned to `0.7.23`", "Do not upgrade" |
| `REVIEW.md:91,131` | a bump past 0.7.23 is `[important]` |
| `EVALS.md:79` | a reviewer who misses such a bump gets a deduction |

The code runs 0.10.1. The version appears 6 times: `releases/crds/kagent-crds.yaml:15` and `releases/kagent.yaml:15,46,70,73,76`. The `+` problem is handled by the postRenderer and the explicit image tags. The root cause is also different from what the docs say: the `+` comes from Flux appending the OCI digest, not from kagent embedding build metadata (`releases/kagent.yaml:25-39`).

A reviewer who follows REVIEW.md would flag current `main`, and the EVALS rubric would penalize a reviewer who didn't.

`CODEBASE.md:115` says to update "all three tag references" on a bump. For kagent there are six.

**Direction.** Replace the pin rule with the real invariant: every kagent version reference, chart, CRD chart, postRenderer label, and the three image tags, must move together.

### 12. ReferenceGrant rule targets the wrong case

**Read**, against the Gateway API spec. The docs say an HTTPRoute needs a ReferenceGrant in its own namespace to attach to a Gateway in another namespace:

- `CODEBASE.md:98,109,129`
- `REVIEW.md:60,87,110,125`
- `EVALS.md:77`, plus Examples D and E

That isn't how Gateway API works:

- **Route to Gateway attachment** is allowed by the Gateway listener's `allowedRoutes.namespaces`. Here that's `from: All` (`releases/agentgateway.yaml:49-51`).
- **ReferenceGrant** authorizes references *into* another namespace: a route's `backendRefs` to a Service, or a Gateway's `certificateRefs` to a Secret. It must live in the **target** namespace.

The existing grant (`releases/kagent.yaml:92-104`) lets HTTPRoutes in `kagent` reference Services in `kagent`. That's the same namespace, so the grant does nothing. It's harmless, but it teaches the wrong pattern.

The review rules therefore mark a missing route-to-Gateway grant as `[critical]`, which is a false positive. They have no rule for the case that really fails: a `backendRef` to a Service in another namespace with no grant there. That route reports `ResolvedRefs=False` with reason `RefNotPermitted`.

**Direction.** Rewrite the rule in all three docs. Optionally remove the no-op grant.

### 13. gateway-api CRDs are no longer a HelmRelease

**Read.** The docs describe the old setup:

| Doc | Says |
|---|---|
| `CODEBASE.md:145` | Gateway API CRDs are "managed via HelmRelease (`ghcr.io/den-vasyliev/gateway-api-crds:1.4.0`)" |
| `CODEBASE.md:75-76` | the layout lists `gateway-api-crds.yaml` under "CRD HelmReleases" |
| `README.md:65` | "CRD HelmReleases: gateway-api, agentgateway, kagent" |

Today it's a GitRepository for `kubernetes-sigs/gateway-api` at `v1.6.2` plus a nested Flux Kustomization on `./config/crd/experimental` (`releases/crds/gateway-api-crds.yaml:24-55`). `CODEBASE.md:22` already says v1.6.2 experimental, so the doc contradicts itself.

The REVIEW checklist for new CRDs (`REVIEW.md:65`, `install.crds: CreateReplace`) doesn't cover this non-Helm pattern.

### 14. Release docs predate the pin and per-branch streams

**Verified** by grep over the docs.

| Doc | Says | Current state |
|---|---|---|
| `CODEBASE.md:68` | the RSIP filter is `^\d+\.\d+\.\d+$` | `^0\.6\.5$` (finding 3) |
| `CONTRIBUTING.md:51` | the RSIP filter (`^\d+\.\d+\.\d+$`) and the `make push` bump logic "must stay in sync" | `^0\.6\.5$` (finding 3) |
| README, CODEBASE, CONTRIBUTING, REVIEW, EVALS | nothing about `var.releases_artifact` or the `releases-<branch>` streams from `390671c` | a feature-branch workflow now depends on them |
| `README.md:42`, `CODEBASE.md:41` | the cluster "polls `.../releases`" | it polls `<oci_registry>/<releases_artifact>` |

A reviewer using REVIEW.md:68 ("Does the filter regex still match only clean semver?") would pass the pinned filter without comment, but would have no rule for keeping `flux-push.yaml` and `releases_artifact` in agreement.

**Direction.** Document the two streams and the temporary pin in CODEBASE.md, including how a branch cluster opts in and when the pin is lifted. Add a REVIEW rule: changes to the stream naming in `flux-push.yaml` must match `variables.tf`.

### 15. Component inventory is incomplete

**Read.** Components and files missing from the docs:

| Location | Missing |
|---|---|
| `CODEBASE.md:72-88` (layout) | `phoenix.yaml`, `qdrant.yaml`, `agentregistry.yaml`, `bootstrap/flux-instance.yaml`, `scripts/fix-egress.sh`, `.github/workflows/terraform-pr-check.yml` |
| `CODEBASE.md:92-98` (component roles) | phoenix, qdrant, agentregistry |
| `CODEBASE.md:15-26` (tech stack) | agentregistry |
| `README.md:9-17` (what's included) | agentregistry |
| `README.md:37-47` (how it works) | everything under `releases/` except agentgateway and kagent |

### 16. Smaller drift in CONTRIBUTING and README

**Read.**

- `CONTRIBUTING.md:41` says to record design decisions in `CLAUDE.md`, but `CLAUDE.md` is gitignored (`.gitignore:36`), so those notes never reach the repo. The section that exists for them is `CODEBASE.md` "Key Design Decisions".
- `CONTRIBUTING.md:23` clones `den-vasyliev/abox`. That's fine if upstream is canonical; otherwise it's wrong for this fork.
- `CONTRIBUTING.md:60-69` asks for conventional commits (`feat:`, `fix:`), and one example mentions kagent 0.7.24. Recent history uses `<area>: <summary>` instead, for example `releases: widen the Phoenix startup probe` and `ci: give each branch its own releases artifact repository`.
- `README.md:25` says `make run` "installs OpenTofu and k9s". It also installs kind (with sudo) and cloud-provider-kind, may change the host iptables policy (with sudo), and edits `~/.bashrc`.

---

## Open questions (not bugs)

### 17. Phoenix, qdrant, and agentregistry have no route

Phoenix serves a UI on `:6006` (`releases/phoenix.yaml:40`). agentregistry runs with `disableAuth: true` (`releases/agentregistry.yaml:34`). Neither has an HTTPRoute, and neither does qdrant, so all three are reachable only through `kubectl port-forward`. REVIEW.md suggests routes for UIs (`REVIEW.md:95`).

Two things to keep in mind when adding routes:

- kagent owns the `/` catch-all, so new routes need a hostname or a more specific prefix.
- Routing agentregistry puts an unauthenticated service on the LoadBalancer IP. It's local-only, but that's worth knowing.

### 18. agentregistry chart and image versions differ

The chart is `0.5.16` and the image is `0.5.18` (`releases/agentregistry.yaml:15,33`). Both came in commit `263aec5` with no explanation. Either the chart lags the image on purpose, or a bump was missed.

### 19. qdrant is the only HelmRepository source

Every other chart comes from an OCIRepository through `chartRef`. qdrant uses an HTTPS HelmRepository through `chart.spec` (`releases/qdrant.yaml:6-30`). That isn't wrong. But the version-pinning rule is written in terms of `ref.tag` (`CODEBASE.md:114`, `REVIEW.md:59`), so a reviewer applying it literally wouldn't catch `chart.spec.version: "*"` here.

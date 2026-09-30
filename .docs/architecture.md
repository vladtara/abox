# Architecture

How abox goes from `tofu apply` to a running stack, as of commit `390671c`. Back to [index](README.md).

The work happens in two stages:

1. **OpenTofu** (`bootstrap/`) creates the cluster, installs Flux, and creates two Flux Operator objects. Its job ends there.
2. **Flux** reconciles everything else from an OCI artifact of `releases/`. Nothing polls Git, so there are no deploy keys or tokens.

## Stage 1: bootstrap (OpenTofu)

```
tofu apply  (bootstrap/)
  → kind_cluster.this              cluster.tf:4    "abox": 1 control-plane + 2 workers, kube-proxy ipvs
  → module.flux_operator           flux.tf:8       controlplaneio-fluxcd/flux-operator-bootstrap v0.8.0
        in-cluster Job: helm install flux-operator chart 0.59.0      (variables.tf:33)
        apply bootstrap/flux-instance.yaml (create-if-missing), wait for Ready
  → kubectl_manifest.rsip          flux.tf:29      ResourceSetInputProvider "releases-image"
  → kubectl_manifest.rset          flux.tf:62      ResourceSet "releases"
```

The steps run strictly in order through `depends_on`. RSIP and ResourceSet are applied after the module because their CRDs ship with the flux-operator chart the Job installs (`flux.tf:27-28`).

### Providers

| Provider | Constraint | Locked | Used for |
|---|---|---|---|
| `tehcyx/kind` | `>= 0.8` | 0.11.0 | the cluster (embeds kind; no CLI needed) |
| `hashicorp/helm` | `>= 3.0` | 3.3.0 | required by the bootstrap module |
| `hashicorp/kubernetes` | `>= 3.0` | 3.2.1 | required by the bootstrap module |
| `gavinbunney/kubectl` | `>= 1.14` | 1.19.0 | RSIP and ResourceSet: skips CRD schema validation at plan time, which is what makes a single-pass apply possible |

OpenTofu `>= 1.11.0` is required because the module uses write-only attributes (`providers.tf:2-4`). The lock file is keyed on `registry.opentofu.org`.

### Variables (`bootstrap/variables.tf`)

| Variable | Default | Notes |
|---|---|---|
| `cluster_name` | `abox` | also hard-coded in `Makefile:24` (`fix-egress.sh verify abox`) |
| `oci_registry` | `oci://ghcr.io/den-vasyliev/abox` | upstream owner, not this fork; see [finding 4](findings.md#4-fork-and-registry-mismatch) |
| `releases_artifact` | `releases` | the artifact repository under `oci_registry`. A feature branch sets `releases-<branch>` to match CI (added in `390671c`) |
| `releases_version` | `0.1.0` | RSIP `defaultValues.tag` |
| `flux_operator_version` | `0.59.0` | pinned; the module default would float to latest |
| `bootstrap_revision` | `1` | bump to force the bootstrap Job to re-run |

### FluxInstance (`bootstrap/flux-instance.yaml`)

- Distribution `2.x` from `ghcr.io/fluxcd`, manifests from `flux-operator-manifests:latest` (both floating).
- Controllers: source, kustomize, helm, notification. There is no image-automation.
- `networkPolicy: true`, `multitenant: false`.
- `.spec.sync` is deliberately unset. The ResourceSet below drives syncing instead of a Git source.

## Stage 2: reconcile (Flux)

```
ResourceSetInputProvider releases-image   (flux-system)                    flux.tf:33-56
  type OCIArtifactTag on <oci_registry>/<releases_artifact>
  includeTag ^0\.6\.5$ (pinned), limit 1, every 5m, default tag 0.1.0
    → ResourceSet releases  (flux-system), templated with << inputs.tag >>  flux.tf:66-115
        OCIRepository releases       interval 2m, ref.tag = inputs.tag
        Kustomization releases-crds  path ./crds, prune, wait, interval 2m
        Kustomization releases       path ./,     prune, wait, interval 2m, retry 30s,
                                     dependsOn releases-crds
```

The filter used to be the open `^\d+\.\d+\.\d+$`. Commit `ccdd528` pinned it to `0.6.5`, `main`'s last release (= `4bbe4d3`), because feature-branch tags 0.6.6 to 0.8.9 had been published into the same repository and were outranking `main`. The comment at `flux.tf:43-51` says to widen it again later. See [finding 3](findings.md#3-releases-are-frozen-at-065).

### Artifact streams (since `390671c`)

`flux-push.yaml:37-53` decides which OCI repository a tag goes to:

| Tagged commit is on | Published to |
|---|---|
| `origin/main` | `<registry>/releases` |
| any other remote branch | `<registry>/releases-<last path segment of branch, lowercased>`, e.g. `feat/otel-demo` → `releases-otel-demo` |

A cluster follows one stream, chosen by `var.releases_artifact`. The RSIP tag filter is still hard-coded, though, so a branch cluster only accepts `0.6.5` too.

### What each Kustomization applies

**Verified** by rendering with `kubectl kustomize`:

| Kustomization | Objects |
|---|---|
| `releases-crds` (`releases/crds/`) | Namespaces `agentgateway-system`, `kagent`; OCIRepositories + HelmReleases for `agentgateway-crds`, `kagent-crds`; GitRepository `gateway-api` + nested Kustomization `gateway-api-crds` |
| `releases` (`releases/`) | Namespaces `agentgateway-system`, `kagent`, `phoenix`, `qdrant`, `agentregistry`; 4 OCIRepositories + 1 HelmRepository; HelmReleases for agentgateway, kagent, phoenix, qdrant, agentregistry-inventory; Gateway, HTTPRoute, ReferenceGrant |

The `agentgateway-system` and `kagent` Namespaces appear in both columns. See [finding 6](findings.md#6-two-kustomizations-own-the-same-namespaces).

Placement follows two rules. Sources (OCIRepository, HelmRepository, GitRepository) live in `flux-system`, and each HelmRelease lives in its component's namespace.

## Component inventory

| Component | Kustomization | Namespace | Source | Version | Image overrides | `dependsOn` | Gateway route |
|---|---|---|---|---|---|---|---|
| Gateway API CRDs | releases-crds | `flux-system` (nested Kustomization) | GitRepository `github.com/kubernetes-sigs/gateway-api` | v1.6.2, experimental channel | n/a | none | n/a |
| agentgateway-crds | releases-crds | `agentgateway-system` | OCI `ghcr.io/kgateway-dev/charts/agentgateway-crds` | v2.2.1 | none | none | n/a |
| kagent-crds | releases-crds | `kagent` | OCI `ghcr.io/kagent-dev/kagent/helm/kagent-crds` | 0.10.1 | none | none | n/a |
| agentgateway | releases | `agentgateway-system` | OCI `ghcr.io/kgateway-dev/charts/agentgateway` | v2.2.1 | none | agentgateway-crds | owns Gateway `agentgateway-external` |
| kagent | releases | `kagent` | OCI `ghcr.io/kagent-dev/kagent/helm/kagent` | 0.10.1 | `tag`, `controller.image.tag`, `ui.image.tag` = 0.10.1 | kagent-crds | HTTPRoute `kagent`: `/api`, `/` |
| phoenix | releases | `phoenix` | OCI `registry-1.docker.io/arizephoenix/phoenix-helm` | 12.0.10 | none | none | none |
| qdrant | releases | `qdrant` | HelmRepository `https://qdrant.github.io/qdrant-helm` | 1.19.1 | none | none | none |
| agentregistry-inventory | releases | `agentregistry` | OCI `ghcr.io/den-vasyliev/charts/agentregistry` | 0.5.16 | `image.tag` = 0.5.18 | none | none |

Notable values:

- **kagent** (`releases/kagent.yaml:40-90`) sets four things:
  - a postRenderer that rewrites `app.kubernetes.io/version`
  - explicit image tags
  - the istio and cilium agents disabled
  - `providers.default: openAI` with a placeholder key ([finding 5](findings.md#5-kagent-openai-key-is-a-placeholder))
- **phoenix** (`releases/phoenix.yaml:31-50`): ingress disabled; startup probe widened to 5s x 120, about a 10 minute budget.
- **agentregistry** (`releases/agentregistry.yaml:31-34`): `disableAuth: true`.
- **agentgateway**, **qdrant**: chart defaults.

phoenix, qdrant, and agentregistry have no `dependsOn`, which is correct: as far as the manifests show, they only create core Kubernetes types, so there's no CRD to wait for (**read**).

## Traffic path

```
client → <LoadBalancer IP>:80
           Service in agentgateway-system; IP assigned by cloud-provider-kind (setup.sh:86-94)
  → Gateway agentgateway-external, listener "http" :80, allowedRoutes from: All
                                                           (releases/agentgateway.yaml:38-51)
    → HTTPRoute kagent  (namespace kagent)                 (releases/kagent.yaml:106-131)
        /api  → Service kagent-controller :8083
        /     → Service kagent-ui :8080        (catch-all prefix)
```

- The `agentgateway` GatewayClass isn't defined in this repo. Commit `1e5bfd1` removed the repo-managed one so the chart could supply it. The README still says the releases include it (`README.md:45`).
- `/` is a catch-all owned by kagent. Any new route on the same listener needs a hostname or a path prefix more specific than `/`.
- Phoenix, qdrant, and agentregistry are reachable only through `kubectl port-forward`.

## Why the workarounds exist

Each entry below is documented in a code comment or commit message at the location given.

| Workaround | Where | Reason |
|---|---|---|
| RSIP filter pinned to `0.6.5` | `bootstrap/flux.tf:43-52` | Feature-branch tags 0.6.6 to 0.8.9 were in the same artifact repository and outranked `main`'s. |
| Per-branch artifact repositories | `.github/workflows/flux-push.yaml:31-53` | Keeps feature-branch tags out of `main`'s stream from now on. |
| kagent `app.kubernetes.io/version` postRenderer | `releases/kagent.yaml:25-48` | Flux appends the OCI digest to the chart version as semver build metadata (`0.10.1+dc7fc6109072`). kagent's Chart.yaml has no `appVersion`, so the label inherits the `+`, which Kubernetes rejects. |
| kagent explicit image tags | `releases/kagent.yaml:59-76` | The same `+` leaks into the default image tags and causes `InvalidImageName`. |
| agentgateway held at v2.2.1 | `releases/agentgateway.yaml:13-15` | The v2.2.2 and v2.2.3 charts reference controller images that were never published, so they fail with `ErrImagePull`. |
| Gateway API experimental channel | `releases/crds/gateway-api-crds.yaml:8-22` | agentgateway's informers are typed to `v1alpha2` TLSRoute and TCPRoute. Only the experimental channel serves `v1alpha2`. |
| Gateway API CRDs inlined from Git | `releases/crds/gateway-api-crds.yaml:3-6` | The old `den-vasyliev/gateway-api-crds` chart only ever had a 1.4.0 tag. |
| GitRepository keeps both channels | `releases/crds/gateway-api-crds.yaml:34-41` | Narrowing it to one channel would mean a channel switch doesn't change the artifact, and the Kustomization would fail with "path not found". |
| Phoenix startup probe | `releases/phoenix.yaml:35-50` | First-boot Alembic migrations take longer than the chart's 31s startup budget on KinD, so the pod restarts forever. |
| `gavinbunney/kubectl` for RSIP and ResourceSet | `bootstrap/flux.tf:29,62`; `CODEBASE.md:141` | `hashicorp/kubernetes` validates against CRD schemas at plan time, and those CRDs don't exist before the first apply. |
| Create-if-missing bootstrap | `bootstrap/flux.tf:4-7` | Objects adopted by Flux are left alone on later applies, so OpenTofu never fights reconciliation. |
| `fix-egress.sh` | `scripts/fix-egress.sh:2-15` | On Codespaces, a stale `iptables-legacy` FORWARD DROP silently blocks egress from user-defined Docker bridges, including the `kind` network. |

Generalization of the kagent rows (**read**): any chart pulled through an OCIRepository whose templates fall back to `.Chart.Version` for labels or image tags will hit the same `+` problem. Check for this when adding a component.

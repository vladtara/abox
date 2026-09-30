# Workflows

Prerequisites, local commands, the release pipeline, CI, and the review and eval harness, as of commit `390671c`. Back to [index](README.md).

## Prerequisites

The repo expects these tools but doesn't install them: Docker, `kubectl`, `bash`, `curl`, and `sudo` (for the kind install and the iptables fix). The README quickstart uses `kubectl` (`README.md:28-30`). CONTRIBUTING asks for `flux get all`, which needs the `flux` CLI (`CONTRIBUTING.md:57`).

`make run` installs:

| Tool | Version | How | Where |
|---|---|---|---|
| OpenTofu | latest | `get.opentofu.org` install script, standalone | `scripts/setup.sh:30` |
| kind CLI | v0.33.0 | download, then `sudo install` to `/usr/local/bin` | `scripts/setup.sh:36-45` |
| k9s | latest | `webi.sh` | `scripts/setup.sh:49` |
| cloud-provider-kind | 0.11.1 | tarball into `/tmp`, started with `nohup` | `scripts/setup.sh:86-97` |

The cluster itself is created by the `tehcyx/kind` provider, which embeds kind. The kind CLI is only for node-level work such as `kind get nodes` and `kind load docker-image`.

## Make targets

`help` is the first target, so a bare `make` prints the list.

| Target | Does | Notes |
|---|---|---|
| `run` | `bash scripts/setup.sh` | the full path, described below |
| `tools` | installs OpenTofu, k9s, and kind | not cloud-provider-kind; pipes webi to `bash`, where `setup.sh` uses `sh` |
| `tofu` | `tofu init` in `bootstrap/` | |
| `apply` | `tofu apply -auto-approve` in `bootstrap/` | |
| `down` | `tofu destroy -auto-approve` in `bootstrap/` | doesn't stop cloud-provider-kind |
| `push` | fetches tags from `origin`, bumps the patch of the highest tag, tags HEAD, pushes `main` and the tag | picks the wrong base tag; see [findings 1 and 2](findings.md#1-make-push-derives-the-version-from-the-wrong-tags) |
| `fix-egress` | runs `fix-egress.sh`, then `fix-egress.sh verify abox` | |

## `scripts/setup.sh`, step by step

1. Tees all output to `/tmp/setup.log`.
2. Detects the OS (linux or darwin) and arch (amd64 or arm64). On an unsupported platform it skips the binary installs.
3. Installs OpenTofu, then the kind CLI, then k9s.
4. Appends the aliases `kk`, `tf`, and `k` to `~/.bashrc`. This happens on every run.
5. Runs `fix-egress.sh`.
6. Runs `tofu init` and `tofu apply -auto-approve` in `bootstrap/`.
7. Runs `fix-egress.sh verify abox`. A failure here only logs a warning.
8. Downloads cloud-provider-kind and starts it in the background.

It doesn't wait for Flux to reconcile. When the script exits, the HelmReleases may still be installing. Check with `flux get all -A` or `kubectl get helmrelease -A`.

## `scripts/fix-egress.sh`

- **Problem.** On hosts that have both iptables backends (seen on Codespaces), a stale `iptables-legacy` FORWARD policy of DROP blocks egress from every user-defined Docker bridge, including `kind`. Image pulls then time out rather than fail fast.
- **Fix.** It sets the legacy FORWARD policy to ACCEPT. It does nothing when:
  - the host isn't Linux
  - `iptables-legacy` isn't installed
  - there's no passwordless sudo
  - the policy is already permissive
- **`verify NAME`.** It probes raw TCP to `1.1.1.1:53` from each kind node container through `docker exec`. It avoids DNS, so a failure points at routing rather than name resolution.

## Release pipeline

```
make push                                                   Makefile:35-44
  → git tag vX.Y.Z (on HEAD) ; git push origin main vX.Y.Z
  → .github/workflows/flux-push.yaml   (on: push tags v*)
      resolve stream: tagged commit on origin/main → releases, else releases-<branch>   :37-53
      flux push artifact oci://ghcr.io/<owner>/<repo>/<stream>:X.Y.Z --path ./releases  :59
      flux tag artifact  .../<stream>:X.Y.Z@<digest> --tag latest                       :67-71
  → RSIP releases-image (every 5m) on <oci_registry>/<releases_artifact>
      currently accepts only 0.6.5 (flux.tf:52), so new tags are ignored
  → ResourceSet updates OCIRepository ref.tag → pull (2m) → releases-crds → releases
```

- The git tag has a `v` prefix, and CI strips it for the OCI tag (`flux-push.yaml:58`).
- While the filter is pinned, **no release reaches a default cluster**. See [finding 3](findings.md#3-releases-are-frozen-at-065). Once it's widened back to `^\d+\.\d+\.\d+$`, it ignores `latest`, pre-releases, and build metadata.
- Neither the RSIP nor the OCIRepository has a `secretRef`, so pulls are anonymous and the GHCR package must be public (**read**).
- `make push` tags HEAD but pushes `main`. If you're on another branch, the tagged commit only reaches the remote through the tag. See [finding 8](findings.md#8-ci-hygiene) for what CI then does with it.
- With an open filter, worst-case latency after CI finishes is about 9 minutes: RSIP 5m, OCIRepository 2m, Kustomization 2m.
- There is no rollback target. With `limit: 1`, the RSIP follows the highest matching tag, so rolling back means publishing a new, higher tag with the old content, or narrowing the filter the way `ccdd528` did (**read**).

## PR checks (`.github/workflows/terraform-pr-check.yml`)

The workflow triggers on PRs to `main` or `master` that touch `**.tf` or `**.tfvars`, and runs two jobs.

| Job | Steps |
|---|---|
| `terraform-check` | `dflook/terraform-fmt-check` (repo tree), `dflook/terraform-validate` (default path, the repo root, which has no `.tf` files), `dflook/terraform-plan` on `bootstrap/` |
| `security-check` | Checkov (`CKV_AWS_*` only), tfsec (SARIF, hard fail), git-secrets (AWS patterns, cloned from HEAD) |

Changes to `releases/`, `scripts/`, the `Makefile`, or `flux-push.yaml` get no PR checks. See [findings 7 and 8](findings.md#7-no-pr-validation-for-releases).

## Review and eval harness

Three documents form a chain:

| File | Role |
|---|---|
| `CODEBASE.md` | Ground truth: architecture, conventions, forbidden patterns, key design decisions |
| `REVIEW.md` | Prompt for an AI PR reviewer: one consolidated review, severity labels (`[critical]`, `[important]`, `[suggestion]`, `[nit]`), a checklist, and project rules |
| `EVALS.md` | Prompt for grading such a review: start at 10 and deduct per miss or false positive, with 6 labelled examples and a failure-mode table |

- A stale fact in `CODEBASE.md` spreads into both prompts, because REVIEW and EVALS each defer to it.
- Five rules are currently wrong or stale:
  - the kagent 0.7.23 pin ([finding 11](findings.md#11-kagent-0723-pin-is-stale))
  - the ReferenceGrant rule ([finding 12](findings.md#12-referencegrant-rule-targets-the-wrong-case))
  - the namespace-split rule ([finding 6](findings.md#6-two-kustomizations-own-the-same-namespaces))
  - the claim that `make push` avoids patch > 9 ([finding 2](findings.md#2-make-push-rolls-the-patch-past-9))
  - the documented RSIP filter ([finding 14](findings.md#14-release-docs-predate-the-pin-and-per-branch-streams))
- No workflow in this repo invokes REVIEW.md or EVALS.md (**verified** by grep over `.github/`). Running them is a manual or external step.

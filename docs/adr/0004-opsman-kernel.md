# ADR-0004: The opsman kernel

- Status: Proposed
- Date: 2026-09-30
- Part of: [ADR-0001](0001-opsman-in-cluster-agent.md), sub-project 1

## Context

[ADR-0001](0001-opsman-in-cluster-agent.md) makes opsman a Go agent that runs
only in the cluster: the model proposes, a deterministic kernel decides, and
changes go through GitOps behind R0 to R4 risk gates. It left four questions
to this ADR: what triggers an iteration, how many replicas, where the journal
lives, and how a human approves.

What the kernel copies from [glapsfun/opsman](https://github.com/glapsfun/opsman):

- the transition table is data, not code
- every state change is a typed event appended to a journal, and state can be
  rebuilt by replaying it
- gates refuse an event until its artifact validates
- budgets are enforced in the same step as the transition
- each role sees only the context it needs

What kagent 0.10.1 offers:

- `Agent` of type `BYO` runs our own image, with `extraContainers` and
  `volumes` ([ADR-0003](0003-embeddings-in-cluster.md) uses both)
- Declarative Agents: prompt, tools and model as YAML, called over A2A
- a Go ADK (`go/adk`) on Google's ADK for Go, with HITL tool approvals

kagent `main` is at 1.0.0-alpha5 (2026-09-27) with a new `v1alpha3` API
("BYO Harness", `ScheduledRun`). abox runs 0.10.1 on `v1alpha2`, so a tight
coupling to kagent's Go libraries would move with that rework.

## Decision

### Scope of v1

The kernel proves itself on one loop: **detect a problem, diagnose it,
propose a fix as a pull request, and after a human merges it, release it and
confirm the problem is gone.** That exercises every gate once.

Not in v1: memory in Qdrant and Neo4j (sub-project 2), skill changes
(sub-project 3), satellites (sub-project 4), tasks sent by humans over A2A.

### The choices

| Question | Decision |
|---|---|
| What triggers an iteration | Watches on Flux objects, Pods and Events feed detectors written as plain code. Only a new, unhandled finding starts a Run and spends LLM tokens. A resync every 10 minutes catches what a watch missed. |
| Where state and the journal live | A `Run` custom resource per finding. `status` holds the phase and the append-only journal; artifacts go in ConfigMaps owned by the Run. |
| Where the reasoning runs | Three kagent Declarative Agents called over A2A. The kernel has no LLM client and validates every answer against a schema. |
| How a human approves | Merging opsman's pull request. Nothing else counts as approval. |
| Who releases | opsman, after the merge: it tags the merge commit, waits for Flux, and re-runs the detector. |
| Replicas | One, with a Lease for leader election, so a rolling update never has two writers. |
| How it is built | A controller-runtime operator. |

A controller-runtime operator was chosen over plain client-go with a
hand-written loop (which would re-implement requeue, backoff and leader
election) and over two binaries, one for detecting and one for executing
(stronger isolation, but two things to ship for no v1 gain; the package
boundary below allows that split later).

The Go ADK was not chosen for the roles: it would put the LLM loop inside the
kernel and tie the kernel to a library kagent is reworking for 1.0. A2A is a
protocol, so the roles can move to kagent 1.0 without the kernel changing.

## Design

### Components

A Go module at `opsman/` (`github.com/vladtara/abox/opsman`), beside
`mcp/qdrant-mcp`.

| Package | Job | I/O |
|---|---|---|
| `api/v1alpha1` | `Run` CRD types | none |
| `machine` | transition table (embedded data), gates and budgets as pure functions: `Next(run, event) (state, error)`, `Replay(events)` | none |
| `detect` | detectors as pure functions from watched objects to `Finding{fingerprint, object, summary, evidence}`; a small controller debounces and dedupes against existing Runs | read |
| `reconcile` | the `Run` reconciler: asks `machine` which step the state needs, runs one step through `actions`, records the event | read, write |
| `roles` | A2A client for the role agents: send a task, poll it, validate the JSON answer against its schema | network |
| `gitops` | GitHub App client: find the source file of an object, branch, commit, open a PR, watch for the merge, push the tag | network |
| `policy` | classify a proposed diff (allowed or refused), enforce write scope and deny rules, scan for secrets | none |
| `a2a` | minimal A2A server so kagent registers opsman as a `BYO` Agent; v1 answers "status" with the list of Runs | network |
| `telemetry` | one OTel trace per Run, a span per transition, role calls as child spans | network |

The three roles are kagent Declarative Agents in `releases/opsman-roles.yaml`:

| Role | Tools | Gets | Returns |
|---|---|---|---|
| `opsman-diagnoser` | read-only k8s: get, describe, logs, events | the finding and its evidence | root cause, evidence, whether it is fixable in `releases/` |
| `opsman-fixer` | none | finding, diagnosis, the source files | the full new content of each file it changes |
| `opsman-oracle` | none | finding, diagnosis, the diff | verdict (approve or reject) with reasons |

The kernel, not a role, finds the file in `releases/` that defines the failing
object, by matching kind and name in the repository tree, and passes it to the
fixer. A role never chooses what it may touch.

Data flow:

```text
watch event -> detector -> Finding -> new Run (unless its fingerprint is open or cooling down)
Run: diagnoser (A2A) -> fixer (A2A) -> policy -> oracle (A2A) -> pull request
merge -> tag -> Flux applies -> detector again -> COMPLETED, or back to diagnosis
```

### State machine

Each state names what opsman is waiting for. On entering a state the
reconciler performs that state's action once and records a self-event
(`RoleRequested` with the A2A task id, `PROpened` with the PR number,
`TagPushed` with the tag). Later reconciles only poll, so no action runs twice.

| From | Event | To | Checked before accepting |
|---|---|---|---|
| (new) | `RunStarted` | DETECTED | fingerprint not open, cooldown over, daily cap not reached |
| DETECTED | `RoleRequested` (diagnoser) | DIAGNOSING | no other active Run |
| DIAGNOSING | `Diagnosed` | PROPOSING | schema; names the finding's object; has evidence |
| DIAGNOSING | `NeedsHuman` | BLOCKED | a reason (for example the cause is outside `releases/`) |
| PROPOSING | `FixProposed` | JUDGING | the fix gate below |
| PROPOSING | `FixRejected` | PROPOSING | written by the kernel when the fix gate fails; the reason goes into the next fixer attempt |
| JUDGING | `OracleApproved` | AWAITING_MERGE | schema; the fix gate again on the same diff |
| JUDGING | `OracleRejected` | PROPOSING | counts as a fix attempt |
| AWAITING_MERGE | `PRMerged` | RELEASING | merged into `main` |
| AWAITING_MERGE | `PRClosed` | ABANDONED | closed without merge: a human "no" |
| RELEASING | `Applied` | VERIFYING | the tag gate passed and `TagPushed` is recorded; Flux's `releases` Kustomization reports that revision applied |
| VERIFYING | `FindingCleared` | COMPLETED | the detector reports nothing for the fingerprint for 5 minutes |
| VERIFYING | `FindingPersists` | DIAGNOSING | counts as a round; the previous attempt goes into the diagnosis |
| DETECTED to JUDGING | `FindingCleared` | ABANDONED | it fixed itself |
| any open state | `BudgetExceeded`, `RunBlocked` | BLOCKED | |
| BLOCKED | `Resumed` | the state it came from | a human set `spec.decision` |
| any open state | `Abandoned` | ABANDONED | a human set `spec.decision` |

COMPLETED and ABANDONED are terminal and accept no events. BLOCKED is not
terminal. A finding that clears while the pull request is open changes
nothing: the human decides by merging or closing it.

Budgets, set when a Run is created:

| Budget | Default |
|---|---|
| attempts per role call (timeout, invalid JSON, schema violation) | 3 |
| fix attempts (fix gate or oracle rejections) | 2 |
| verify rounds | 2 |
| wall time per Run | 2 hours |
| cooldown per fingerprint after a Run ends | 1 hour |
| Runs started per day | 10 |
| active Runs at a time | 1; newer Runs wait in DETECTED |

A budget that runs out moves the Run to BLOCKED; nothing fails silently.

Risk classes:

| Class | In v1 |
|---|---|
| R0 | watching, detecting, diagnosing |
| R1 | writing `Run` status and artifact ConfigMaps |
| R2 | pushing a branch and opening a pull request |
| R3 | pushing the tag, approved by the merge |
| R4 | anything the fix gate refuses; never runs |

### Data

```yaml
apiVersion: opsman.abox.dev/v1alpha1
kind: Run
metadata:
  generateName: run-
  namespace: opsman
  labels:
    opsman.abox.dev/fingerprint: 3f9c1a7e5b2d4c80
    opsman.abox.dev/detector: flux-not-ready
spec:                        # immutable after creation, except decision
  finding:
    detector: flux-not-ready
    object: {apiVersion: helm.toolkit.fluxcd.io/v2, kind: HelmRelease, namespace: otel-demo, name: opentelemetry-demo}
    summary: "not Ready for 12m: InstallFailed"
    evidence: "<conditions and recent Events, at most 8KB>"
    detectedAt: "2026-10-01T09:00:00Z"
  limits: {roleAttempts: 3, fixAttempts: 2, rounds: 2, timeout: 2h}
  decision: {action: "", reason: ""}
status:
  phase: DIAGNOSING
  returnTo: ""
  counters: {fixAttempts: 0, rounds: 0, roleAttempts: {diagnoser: 1}}
  pending: {role: diagnoser, taskID: "...", since: "..."}
  pr: {number: 0, branch: "", mergedSHA: ""}
  release: {tag: "", appliedRevision: ""}
  events:
    - {seq: 1, type: RunStarted, to: DETECTED, at: "...", traceID: "..."}
    - {seq: 2, type: RoleRequested, from: DETECTED, to: DIAGNOSING, at: "...", payload: {role: diagnoser, taskID: "..."}}
```

- **Fingerprint:** `sha256(detector | kind | namespace | name | reason class)`,
  first 16 hex characters. Not the UID, so a recreated object is the same
  finding; a new failure reason is a new finding.
- **Artifacts** (a role's raw answer, the proposed files, the verdict) go in a
  ConfigMap `<run>-<seq>` owned by the Run, at most 1 MB. The event holds its
  name and sha256. Deleting the Run deletes them, and nothing leaves the
  cluster before the oracle approves.
- **Size:** budgets cap a Run at about 40 events of at most 2 KB each, so
  status stays under 100 KB.
- **Atomic transitions:** the status update carries the resourceVersion it
  read. On a conflict the reconciler re-reads and decides again, which is safe
  because `machine` is pure.
- **Replay check:** every reconcile runs `machine.Replay(status.events)` and
  compares it with `phase` and `counters`. A mismatch means status was edited
  by hand; the Run goes to BLOCKED with "journal mismatch".
- **Who writes what:** only opsman's ServiceAccount may update `runs/status`.
  CRD CEL rules (`self == oldSelf`) make `spec.finding` and `spec.limits`
  immutable, so a human can only set `spec.decision`. Who did it is in the
  Kubernetes audit log.
- **Retention:** terminal Runs are deleted after 7 days. The trace stays in
  Phoenix.
- **`kubectl get runs`** prints PHASE, OBJECT, DETECTOR, PR and AGE.

### Gates, policy and security

**The fix gate**, on `FixProposed` and again on `OracleApproved`, all plain
code:

- **Scope:** every path under `releases/**`, and none under
  `releases/crds/**`, `releases/opsman*.yaml` (opsman never edits itself),
  `bootstrap/**` or `.github/**`.
- **Refused (R4), so the Run goes to BLOCKED:** a deleted file, a resource
  removed from a file, or any change to Secret, Role, ClusterRole,
  RoleBinding, ClusterRoleBinding, ServiceAccount, CustomResourceDefinition or
  Namespace.
- **Size:** at most 3 files and 200 changed lines.
- **Render:** the changed YAML parses, and `kustomize build releases` succeeds
  in-process on `main` plus the patch.
- **Secrets:** gitleaks-style rules over the diff, the commit message and the
  pull request body.

**The tag gate**, after the merge:

- opsman tags the merge commit, not `main` HEAD, so nothing unapproved rides
  along.
- The newest `v*` tag must be an ancestor of the merge commit. Otherwise a
  human released something newer and opsman's tag would roll it back; the Run
  goes to BLOCKED with "main moved, release by hand".
- The version is the next patch, rolling over to the next minor instead of
  past `.9`, and the new tag must sort highest both as semver and as text.
  `make push` is not used: it tags `main` HEAD and pushes `main`.

**Untrusted input.** Pod logs, Events and annotations are text anyone who can
run a workload can write, and they flow into the role prompts. The diagnoser
has read-only tools and the other roles have none, so no role can act; every
answer must pass its schema; the fix gate is code; and the only approval is a
human merge. Evidence reaches the roles marked as data, not instructions.

**opsman's RBAC**, growing with the detectors:

- cluster-wide get, list and watch on `pods`, `events`, `helmreleases`,
  `kustomizations`, `gitrepositories`, `ocirepositories`
- in namespace `opsman` only: `runs`, `runs/status`, `configmaps`, `leases`
- no Secrets, and no write anywhere else; every change to the cluster goes
  through git

**Known gap.** The diagnoser's tools run as kagent-tools' ServiceAccount, not
opsman's, and that account can read more. v1 gives the diagnoser a strict
tool allowlist and runs the secret scan on everything leaving the cluster. A
dedicated read-only tool server for the diagnoser is a follow-up.

**GitHub.** A GitHub App on `vladtara/abox` with contents write (branches and
tags), pull requests write and metadata read. Its private key is in Secret
`opsman-github`, created by hand and never committed. Branch protection on
`main` requires a human review and does not let the App bypass it; a tag
ruleset lets only the App and humans create `v*` tags.

**LLM traffic.** The role agents' ModelConfig points at agentgateway, not at
the provider directly. If that route does not exist yet, it is a
prerequisite.

### Testing

| Level | What |
|---|---|
| `machine` | table-driven: every transition row, every illegal event, terminal states accept nothing, budgets; property test that `Replay` equals applying events one by one |
| `policy` | golden diffs with expected verdicts: allowed, deleted file, Secret kind, out of scope, too large, `releases/crds/**`, self-edit, leaked token |
| `detect` | fixture objects to expected findings; fingerprints stable across object recreation |
| `gitops` | fake GitHub (`httptest`) and an in-memory repository (go-git): ancestor check, `v1.3.9` to `v1.4.0`, "main moved" |
| `roles` | fake A2A server: valid answers, invalid JSON, schema violations, timeouts |
| `reconcile` | envtest with fake roles and fake GitHub: the happy path, every side path, resourceVersion conflicts, journal mismatch, one active Run |
| end to end | in the Codespace cluster, a canary podinfo HelmRelease (`releases/opsman-canary.yaml`). A human breaks it by PR (a chart version that does not exist); opsman detects, diagnoses and opens a fix; the human merges; opsman tags; Flux applies; the Run ends COMPLETED. A second case: CrashLoopBackOff from a bad argument |
| role evals | 5 findings with known root causes against the live role agents; pass means schema-valid, survives the fix gate, oracle approves. Traces land in Phoenix and MLflow through `genai-collector` |

### Delivery

- **Code:** module `opsman/`, current Go release, controller-runtime,
  controller-gen for the CRD.
- **Image:** `ghcr.io/vladtara/abox/opsman`, multi-arch, distroless static,
  built by `.github/workflows/opsman-image.yaml` like `qdrant-mcp-image.yaml`.
- **Manifests:**
  - `releases/crds/opsman-crds.yaml`, so the CRD applies before anything uses
    it
  - `releases/opsman.yaml`: Namespace `opsman`, the `BYO` Agent, RBAC, and a
    ConfigMap with detector thresholds, budgets, repository and App id
  - `releases/opsman-roles.yaml`: the three role agents
- **Rollout** by a `mode` in the ConfigMap, each step a one-line pull request:

  | Mode | Runs go up to |
  |---|---|
  | `observe` | the diagnosis, then BLOCKED with "observe mode" |
  | `propose` | AWAITING_MERGE; a human tags |
  | `full` | the whole loop; opsman tags |

- **Traces:** the kernel sends `traceparent` on every A2A call, so the role
  agents' LLM spans join the Run's trace and `genai-collector` keeps the whole
  trace for Phoenix and MLflow.

## Consequences

- The kernel is deterministic and testable without an LLM; everything that
  reasons runs elsewhere and is checked before it counts.
- Every change opsman makes is a reviewed pull request, and opsman cannot
  merge its own work.
- Three role agents cost a pod each, and every role call is an A2A hop.
- The loop is only as good as its detectors; anything no detector covers goes
  unnoticed in v1.
- opsman releases after a merge, so a merged opsman PR reaches the cluster
  without a second human step. The tag gate stops it from rolling back a
  newer human release.

## To verify before implementation

- Which namespaces kagent 0.10.1 watches for Agents. If it does not see
  `opsman`, the `BYO` Agent moves to `kagent` and the Runs stay in `opsman`.
  [ADR-0003](0003-embeddings-in-cluster.md) and
  [the cluster ToDo](../todo/embeddings-cluster.md) show the Agent in
  `kagent`; they follow whatever this settles.
- The format of the `releases` Kustomization's `status.lastAppliedRevision`
  for an OCI artifact, which the `Applied` check matches against the tag.
- How the RSIP orders tags that pass `includeTag` (semver or text), which
  decides whether the `.9` rollover is needed or only harmless.
- Whether kagent's default ModelConfig already reaches the provider through
  agentgateway.

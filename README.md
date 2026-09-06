# Signal Desk

An on-call alert triage system. Monitoring systems POST signed webhooks; the
system normalizes, archives, and buffers them; on-call engineers triage the
result behind Microsoft Entra ID SSO.

The application is deliberately small. **The architecture is the subject** — in
particular one decision that the rest of the design follows from.

## The decision

**The alert telling you your cluster is down cannot be received by your cluster.**

So ingest runs on Lambda, outside the cluster, sharing no infrastructure with
it. It validates, archives to S3, and enqueues to SQS — then exits. If the
cluster is down, alerts accumulate in the queue instead of being lost.

Everything else falls out of that:

| Component | Why it is where it is |
|---|---|
| **Lambda** — ingest | Must survive the failure it reports. Spiky, sub-second, idle most of the time. |
| **SQS** — buffer | The decoupling point. Without it, ingest would call the cluster and the independence would be fiction. |
| **S3** — raw archive | Verbatim payloads, written before interpretation. Normalization bugs get replayed, not re-requested from the vendor. |
| **EC2 + k3s** — triage app | Stateful, interactive, long-lived connections. Cluster-shaped work. This is the half that is allowed to be down. |
| **Entra ID** | Human access. Group claims decide who can act, not just who can look. |
| **Argo CD** | Pull-based CD, so nothing outside the cluster holds credentials to it. |
| **GitHub Actions** | Test, build, push, bump a tag. It never touches the cluster. |

```
Monitoring ──HMAC──> [Lambda] ──> [S3]  verbatim archive
  (machines)          ingest   └─> [SQS] ──┐
                                            │  consumed by
Engineer ──Entra OIDC──> [k3s on EC2] ◄─────┘
  (humans)                triage UI + API
```

## Two auth paths, on purpose

Machines cannot do interactive SSO, so senders authenticate with an **HMAC
signature** over the request body, compared in constant time. Humans authenticate
with **OIDC against Entra ID**. Signing in gets you the board; the
`oncall-responders` group claim is what lets you acknowledge and resolve.

Authorization lives in the directory, not in a table this app maintains.

## Running it

Prerequisites: Docker, Terraform.

```powershell
# 1. Start the emulator + console (separate repo, standard setup)
cd ..\floci-ui
docker compose up -d          # emulator :4566, console :4500

# 2. Provision
cd ..\signal-desk
terraform -chdir=terraform init
terraform -chdir=terraform apply -auto-approve

# Floci persists to floci-ui/data, so resources survive a restart and this is
# a no-op on the second run. For a genuine clean slate, clear both sides
# together -- `docker compose down && rm -rf ../floci-ui/data/*` before the
# apply, and delete terraform.tfstate with it. Clearing only one leaves
# Terraform's memory disagreeing with what the emulator holds, and the apply
# fails on BucketAlreadyExists / EntityAlreadyExists.

# 3. Bring up the cluster: k3s + Argo CD
docker compose -f docker-compose.cluster.yml up -d
bash scripts/cluster-up.sh          # installs Argo CD, syncs from GitHub

# 4. Send an alert
$URL = terraform -chdir=terraform output -raw ingest_url
bash scripts/send-alert.sh $URL critical HighErrorRate web-01
```

Board at **http://localhost:30080**, served by the cluster. Resources visible in
the console at **http://localhost:4500**.

Argo CD pulls manifests from this repo on GitHub — the same remote CI pushes to.
There is no local mirror, so a manifest change reaches the cluster only once it
is pushed.

For the inner loop without a cluster, `docker compose up -d --build triage` runs
the app directly against the queue on port 8090.

## What is real and what is not

Everything runs against [Floci](https://github.com/floci-io/floci), a local AWS
emulator — S3, SQS, Lambda and EC2 are all genuinely exercised, and the Lambda
executes in a real container.

**Identity is the exception.** Entra ID has no emulator, so `AUTH_MODE` selects:

- `off` — no auth, for the inner development loop
- `mock` — a local OIDC issuer, so the repo is runnable without a tenant
- `entra` — a real Entra tenant

The mock will assert whatever claims you ask it to, so it proves nothing on its
own — and it is not built yet. **The deployed lab runs `off`.** The OIDC code
paths in `app/auth.py` have never been exercised against a real tenant, so treat
them as written-but-unproven rather than working.

## Known weaknesses

Stated here rather than left for a reviewer to find:

1. **Dedup uses a 60-second window** when the sender supplies no delivery id.
   Two retries straddling a bucket boundary both apply. A vendor-supplied
   delivery id avoids this and is used when present.
2. **The consumer runs in the web process.** Fine for a lab; in production it is
   a separate deployment so the queue keeps draining during a web rollout.
3. **The ID token signature is not verified.** The token arrives over TLS
   directly from the token endpoint under the confidential-client flow, so it is
   not attacker-supplied — but a public client would make verification mandatory.
4. **`_decode_id_token` trusts the `groups` claim shape.** Entra emits group
   *object ids* by default; emitting names requires configuring the app
   registration.
5. **The app cannot scale past one replica.** State lives in a per-pod SQLite
   file, so with two replicas the board's contents depend on which pod answers.
   Found by scaling to 2 and back. Postgres is the fix; the pinned replica count
   is the honest stopgap.
6. **Locally, k3s does not run on the EC2 instance.** Terraform provisions the
   instance, its security group and its bootstrap — and Floci does run it as a
   real privileged Amazon Linux container with the UserData executed. But kubelet
   cannot start inside it:

   ```
   cannot enter cgroupv2 "/sys/fs/cgroup/kubepods" with domain controllers
   ```

   Docker Desktop's WSL2 VM will not delegate cgroup v2 controllers to a nested
   container — writes to `cgroup.procs` return `Operation not supported`. So the
   local cluster runs beside the instance instead of on it. In AWS the UserData
   in `scripts/node-bootstrap.sh` is what runs.
7. **One DNS pin.** `manifests/configmap.yaml` holds the emulator's IP rather
   than its name, because pods resolve through CoreDNS and cannot reach Docker's
   embedded DNS at 127.0.0.11. In AWS this is a real endpoint and the problem
   does not exist. Because Argo CD reads GitHub, a re-pin has to be committed
   and pushed — `scripts/cluster-up.sh` detects the drift and stops rather than
   pushing for you.
8. **Auth is off in the running lab.** See above — nothing protects the board
   locally. The authorization logic exists and is unit-tested; the login flow
   in front of it is not.

## Layout

```
terraform/   S3, SQS, Lambda, IAM, EC2 node + security group
argocd/      the Application: what Argo CD watches, and where
lambda/      ingest: HMAC verify -> normalize -> archive -> enqueue
app/         triage board: SQS consumer + Flask UI + OIDC
manifests/   what Argo CD syncs into the cluster
scripts/     send-alert.sh, node-bootstrap.sh (k3s + Argo CD UserData)
tests/       signature, fingerprint, dedup, ordering
```

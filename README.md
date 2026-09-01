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

# 3. Start the triage app
$env:QUEUE_URL = "http://floci:4566/000000000000/signal-desk-events"
docker compose up -d --build

# 4. Send an alert
$URL = terraform -chdir=terraform output -raw ingest_url
bash scripts/send-alert.sh $URL critical HighErrorRate web-01
```

Board at **http://localhost:8090**. Resources visible in the console at
**http://localhost:4500**.

## What is real and what is not

Everything runs against [Floci](https://github.com/floci-io/floci), a local AWS
emulator — S3, SQS, Lambda and EC2 are all genuinely exercised, and the Lambda
executes in a real container.

**Identity is the exception.** Entra ID has no emulator, so `AUTH_MODE` selects:

- `off` — no auth, for the inner development loop
- `mock` — a local OIDC issuer, so the repo is runnable without a tenant
- `entra` — a real Entra tenant

The mock will assert whatever claims you ask it to, so it proves nothing on its
own. Screenshots in [`docs/`](docs/) are taken against a real tenant. Identity is
the one thing worth not mocking.

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
5. **Locally, k3s does not run on the EC2 instance.** Terraform provisions the
   instance and its bootstrap, which is what happens in AWS. The emulator cannot
   nest a container runtime inside an emulated instance, so the local cluster
   runs alongside instead.

## Layout

```
terraform/   S3, SQS, Lambda, IAM, EC2 node + security group
lambda/      ingest: HMAC verify -> normalize -> archive -> enqueue
app/         triage board: SQS consumer + Flask UI + OIDC
manifests/   what Argo CD syncs into the cluster
scripts/     send-alert.sh, node-bootstrap.sh (k3s + Argo CD UserData)
tests/       signature, fingerprint, dedup, ordering
```

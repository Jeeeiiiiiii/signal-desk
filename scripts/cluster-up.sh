#!/usr/bin/env bash
# Bring the cluster half up: Argo CD, and the first sync from GitHub.
#
# Assumes `docker compose -f docker-compose.cluster.yml up -d` has run and that
# terraform has been applied (the app needs a queue to consume).
set -euo pipefail

# Git Bash rewrites anything that looks like a Unix path into a Windows one
# before handing it to a native binary, so `docker exec ... /tmp/triage.tar`
# arrived inside the container as C:/Users/.../Temp/triage.tar. Nothing here
# mounts a host path, so switching the rewriting off is safe; on Linux and
# macOS the variable is simply ignored.
export MSYS_NO_PATHCONV=1

K3S=signal-desk-k3s-1

echo "==> waiting for the node"
until docker exec "$K3S" kubectl get nodes 2>/dev/null | grep -q " Ready "; do sleep 5; done

echo "==> installing Argo CD"
docker exec "$K3S" kubectl create namespace argocd --dry-run=client -o yaml \
  | docker exec -i "$K3S" kubectl apply -f -
# Server-side apply: the ApplicationSet CRD exceeds the annotation size limit
# that client-side apply relies on. --force-conflicts because a cluster that
# was bootstrapped once with a client-side apply records that as the owner of
# these fields, and a later server-side apply refuses to touch them. We do
# intend to own them: upstream's install.yaml is the only writer here.
docker exec "$K3S" kubectl apply --server-side --force-conflicts -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml >/dev/null
docker exec "$K3S" kubectl wait --for=condition=available --timeout=600s -n argocd deploy/argocd-server

echo "==> side-loading the app image (k3s has its own containerd)"
docker build -q -t signal-desk-triage:local ./app
mkdir -p .cluster && docker save signal-desk-triage:local -o .cluster/triage.tar
docker cp .cluster/triage.tar "${K3S}:/tmp/triage.tar"
docker exec "$K3S" ctr -n k8s.io images import /tmp/triage.tar >/dev/null

# Pods cannot resolve Docker's embedded DNS names, so the emulator's address has
# to be an IP. It is assigned at container start, so it can change between runs.
# Argo CD pulls manifests from GitHub, so a changed address must be pushed there
# before the cluster can see it -- this script will not push on your behalf.
echo "==> checking the pinned emulator address"
FLOCI_IP=$(docker inspect floci-ui-floci-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
sed -i "s|http://[0-9.]*:4566|http://${FLOCI_IP}:4566|g" manifests/configmap.yaml
if ! git diff --quiet manifests/configmap.yaml; then
  echo
  echo "    The emulator moved to ${FLOCI_IP}. manifests/configmap.yaml is updated"
  echo "    locally, but Argo CD reads GitHub. Commit and push it, then re-run:"
  echo
  echo "      git commit -am 're-pin emulator address' && git push origin main"
  echo
  exit 1
fi

echo "==> handing the cluster to Argo CD"
docker cp argocd/application.yaml "${K3S}:/tmp/application.yaml"
docker exec "$K3S" kubectl apply -f /tmp/application.yaml

echo "==> waiting for the first sync"
until docker exec "$K3S" kubectl get application signal-desk -n argocd \
      -o jsonpath='{.status.sync.status}' 2>/dev/null | grep -q Synced; do sleep 6; done

docker exec "$K3S" kubectl get application signal-desk -n argocd
echo
echo "Board: http://localhost:30080"

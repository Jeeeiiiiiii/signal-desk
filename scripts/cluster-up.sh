#!/usr/bin/env bash
# Bring the cluster half up: Argo CD, a git remote, and the first sync.
#
# Assumes `docker compose -f docker-compose.cluster.yml up -d` has run and that
# terraform has been applied (the app needs a queue to consume).
set -euo pipefail

K3S=signal-desk-k3s-1
GITEA=signal-desk-gitea-1
GIT_USER=argocd
GIT_PW=argocd-lab-pw

echo "==> waiting for the node"
until docker exec "$K3S" kubectl get nodes 2>/dev/null | grep -q " Ready "; do sleep 5; done

echo "==> installing Argo CD"
docker exec "$K3S" kubectl create namespace argocd --dry-run=client -o yaml \
  | docker exec -i "$K3S" kubectl apply -f -
# Server-side apply: the ApplicationSet CRD exceeds the annotation size limit
# that client-side apply relies on.
docker exec "$K3S" kubectl apply --server-side -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml >/dev/null
docker exec "$K3S" kubectl wait --for=condition=available --timeout=600s -n argocd deploy/argocd-server

echo "==> creating the git remote"
docker exec -u git "$GITEA" gitea admin user create \
  --username "$GIT_USER" --password "$GIT_PW" --email argocd@example.com \
  --admin --must-change-password=false 2>/dev/null || echo "    user exists"
curl -sf -X POST "http://localhost:3000/api/v1/user/repos" -u "${GIT_USER}:${GIT_PW}" \
  -H "Content-Type: application/json" \
  -d '{"name":"signal-desk","private":false,"auto_init":false}' >/dev/null \
  || echo "    repo exists"

git remote remove gitea 2>/dev/null || true
git remote add gitea "http://${GIT_USER}:${GIT_PW}@localhost:3000/${GIT_USER}/signal-desk.git"
git push gitea HEAD:refs/heads/main

echo "==> side-loading the app image (k3s has its own containerd)"
docker build -q -t signal-desk-triage:local ./app
mkdir -p .cluster && docker save signal-desk-triage:local -o .cluster/triage.tar
docker cp .cluster/triage.tar "${K3S}:/tmp/triage.tar"
docker exec "$K3S" ctr -n k8s.io images import /tmp/triage.tar >/dev/null

echo "==> pinning addresses pods cannot resolve by name"
FLOCI_IP=$(docker inspect floci-ui-floci-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
GITEA_IP=$(docker inspect "$GITEA" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
sed -i "s|http://[0-9.]*:4566|http://${FLOCI_IP}:4566|g" manifests/configmap.yaml
sed -i "s|http://[0-9.]*:3000|http://${GITEA_IP}:3000|g" argocd/application.yaml
git add manifests/configmap.yaml argocd/application.yaml
git diff --cached --quiet || { git commit -qm "re-pin lab addresses"; git push -q gitea HEAD:refs/heads/main; }

echo "==> handing the cluster to Argo CD"
docker cp argocd/application.yaml "${K3S}:/tmp/application.yaml"
docker exec "$K3S" kubectl apply -f /tmp/application.yaml

echo "==> waiting for the first sync"
until docker exec "$K3S" kubectl get application signal-desk -n argocd \
      -o jsonpath='{.status.sync.status}' 2>/dev/null | grep -q Synced; do sleep 6; done

docker exec "$K3S" kubectl get application signal-desk -n argocd
echo
echo "Board: http://localhost:30080"

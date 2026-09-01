#!/usr/bin/env bash
# UserData for the k3s node.
#
# Installs a single-node k3s server and Argo CD, then points Argo CD at the
# manifests directory in this repo. From that moment the cluster's contents are
# whatever git says they are — deploys happen by commit, not by kubectl.
set -euxo pipefail

curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="--write-kubeconfig-mode 644" sh -

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=available --timeout=300s -n argocd deploy/argocd-server

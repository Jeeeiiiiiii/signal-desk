# ---------------------------------------------------------------------------
# Cluster node.
#
# The triage app is stateful and interactive: sessions, a database, a queue
# consumer that must stay warm. That is cluster-shaped work, and the cluster
# needs a node to run on.
#
# This is the half of the architecture that is allowed to be down. Ingest is not.
# ---------------------------------------------------------------------------
data "aws_ami" "node" {
  most_recent = true
  owners      = ["amazon"]

  filter {
    name   = "name"
    values = ["al2023-ami-*-x86_64"]
  }
}

resource "aws_security_group" "node" {
  name        = "${var.name_prefix}-node"
  description = "k3s node: SSH for operators, 6443 for the Kubernetes API, 30080 for the app NodePort"

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Kubernetes API"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Triage app NodePort"
    from_port   = 30080
    to_port     = 30080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "node" {
  ami                    = data.aws_ami.node.id
  instance_type          = var.node_instance_type
  vpc_security_group_ids = [aws_security_group.node.id]

  user_data = file("${path.module}/../scripts/node-bootstrap.sh")

  tags = {
    Name = "${var.name_prefix}-node"
    Role = "k3s-server"
  }
}

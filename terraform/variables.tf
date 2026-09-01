variable "endpoint_url" {
  description = "Base URL of the local AWS emulator. Use http://localhost:4566 from the host, http://floci:4566 from inside a container on floci_default."
  type        = string
  default     = "http://localhost:4566"
}

variable "region" {
  description = "AWS region. Arbitrary against the emulator, but kept explicit so the config is portable to real AWS."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for all resource names."
  type        = string
  default     = "signal-desk"
}

variable "webhook_secret" {
  description = "Shared secret the ingest Lambda uses to verify inbound webhook HMAC signatures. Machines cannot do interactive SSO, so senders sign instead."
  type        = string
  default     = "local-dev-secret-change-me"
  sensitive   = true
}

variable "lambda_endpoint_url" {
  description = "Endpoint the Lambda uses to reach the emulator. The function runs in its own container, so it resolves the emulator by service name rather than localhost."
  type        = string
  default     = "http://floci:4566"
}

variable "node_instance_type" {
  description = "Instance type for the k3s node."
  type        = string
  default     = "t3.medium"
}

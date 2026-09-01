output "raw_bucket" {
  description = "Bucket holding verbatim inbound payloads."
  value       = aws_s3_bucket.raw.bucket
}

output "queue_url" {
  description = "Queue the triage app consumes from."
  value       = aws_sqs_queue.events.url
}

output "dlq_url" {
  description = "Where events land after 5 failed deliveries. Check this first when incidents stop appearing."
  value       = aws_sqs_queue.dead_letter.url
}

output "ingest_function" {
  description = "Name of the ingest Lambda."
  value       = aws_lambda_function.ingest.function_name
}

output "ingest_url" {
  description = "Webhook endpoint. Senders POST here with an X-Signal-Signature header."
  value       = aws_lambda_function_url.ingest.function_url
}

output "node_id" {
  description = "Instance backing the k3s cluster."
  value       = aws_instance.node.id
}

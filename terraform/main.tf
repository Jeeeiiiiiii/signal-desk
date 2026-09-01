# ---------------------------------------------------------------------------
# Raw archive.
#
# Every inbound payload is written here verbatim before anything interprets it.
# When normalization has a bug you re-run it against the archive rather than
# asking the monitoring vendor to resend a week of alerts.
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "raw" {
  bucket = "${var.name_prefix}-raw"
}

resource "aws_s3_bucket_versioning" "raw" {
  bucket = aws_s3_bucket.raw.id
  versioning_configuration {
    status = "Enabled"
  }
}

# ---------------------------------------------------------------------------
# Event buffer.
#
# This queue is what decouples ingest from the cluster. If the cluster is down —
# the exact situation an alert is most likely to be reporting — events accumulate
# here instead of being lost.
# ---------------------------------------------------------------------------
resource "aws_sqs_queue" "dead_letter" {
  name = "${var.name_prefix}-events-dlq"
}

resource "aws_sqs_queue" "events" {
  name = "${var.name_prefix}-events"

  # Long enough for the consumer to persist and delete; short enough that a
  # crashed consumer's messages return quickly.
  visibility_timeout_seconds = 30

  # Hold a backlog for a full working day before giving up on it.
  message_retention_seconds = 86400

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letter.arn
    maxReceiveCount     = 5
  })
}

# ---------------------------------------------------------------------------
# Ingest Lambda.
#
# Deliberately NOT on the cluster. Ingest must survive the failure it reports,
# so it runs on infrastructure with no dependency on the cluster being healthy.
# ---------------------------------------------------------------------------
data "archive_file" "ingest" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda"
  output_path = "${path.module}/.build/ingest.zip"
}

resource "aws_iam_role" "ingest" {
  name = "${var.name_prefix}-ingest"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ingest" {
  name = "${var.name_prefix}-ingest"
  role = aws_iam_role.ingest.id

  # Write-only on both sides. The ingest path never needs to read back what it
  # archived, and never needs to consume from the queue it feeds.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.raw.arn}/*"
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.events.arn
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "*"
      },
    ]
  })
}

resource "aws_lambda_function" "ingest" {
  function_name = "${var.name_prefix}-ingest"
  role          = aws_iam_role.ingest.arn
  handler       = "handler.handler"
  runtime       = "python3.12"

  filename         = data.archive_file.ingest.output_path
  source_code_hash = data.archive_file.ingest.output_base64sha256

  timeout = 10

  environment {
    variables = {
      RAW_BUCKET     = aws_s3_bucket.raw.bucket
      QUEUE_URL      = aws_sqs_queue.events.url
      WEBHOOK_SECRET = var.webhook_secret
      # The Lambda runs in its own container, so it reaches the emulator by
      # service name, not localhost. Not called AWS_ENDPOINT_URL because Lambda
      # reserves parts of the AWS_* namespace.
      SIGNAL_DESK_ENDPOINT = var.lambda_endpoint_url
    }
  }
}

# A public entry point for the webhook senders.
resource "aws_lambda_function_url" "ingest" {
  function_name      = aws_lambda_function.ingest.function_name
  authorization_type = "NONE" # Authentication is the HMAC signature, not IAM.
}

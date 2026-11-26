
resource "random_id" "bucket_suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "legal_documents" {
  bucket = "${var.project_name}-${var.environment}-${random_id.bucket_suffix.hex}"

  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# Prevent public access through bucket or account-level ACL/policy settings.
resource "aws_s3_bucket_public_access_block" "legal_documents" {
  bucket = aws_s3_bucket.legal_documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Disable ACL-based ownership.
resource "aws_s3_bucket_ownership_controls" "legal_documents" {
  bucket = aws_s3_bucket.legal_documents.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Encrypt uploaded objects at rest using S3-managed keys.
resource "aws_s3_bucket_server_side_encryption_configuration" "legal_documents" {
  bucket = aws_s3_bucket.legal_documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Deny requests that do not use HTTPS.
resource "aws_s3_bucket_policy" "require_tls" {
  bucket = aws_s3_bucket.legal_documents.id

  depends_on = [
    aws_s3_bucket_public_access_block.legal_documents
  ]

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"

        Resource = [
          aws_s3_bucket.legal_documents.arn,
          "${aws_s3_bucket.legal_documents.arn}/*"
        ]

        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

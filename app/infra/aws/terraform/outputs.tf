
output "documents_bucket_name" {
  description = "S3 bucket for legal documents"
  value       = aws_s3_bucket.legal_documents.bucket
}

output "documents_bucket_arn" {
  description = "ARN of the legal documents bucket"
  value       = aws_s3_bucket.legal_documents.arn
}

output "aws_region" {
  description = "AWS deployment region"
  value       = var.aws_region
}

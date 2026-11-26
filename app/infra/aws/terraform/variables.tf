
variable "aws_region" {
  description = "AWS region for the Legal RAG infrastructure"
  type        = string
  default     = "eu-west-2"
}

variable "project_name" {
  description = "Project identifier"
  type        = string
  default     = "secure-legal-rag"
}

variable "environment" {
  description = "Deployment environment"
  type        = string
  default     = "demo"
}

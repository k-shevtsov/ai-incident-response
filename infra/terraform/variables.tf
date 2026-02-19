variable "kube_context" {
  description = "Kubernetes context to use"
  type        = string
  default     = "k3d-ai-incident"
}

variable "project_name" {
  description = "Project name used for labeling"
  type        = string
  default     = "ai-incident-response"
}

output "namespaces" {
  description = "Created namespaces"
  value = [
    kubernetes_namespace.app.metadata[0].name,
    kubernetes_namespace.monitoring.metadata[0].name,
    kubernetes_namespace.ai_engine.metadata[0].name,
  ]
}

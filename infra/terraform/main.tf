terraform {
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.30"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.13"
    }
  }
}

provider "kubernetes" {
  config_path    = "~/.kube/config"
  config_context = "k3d-ai-incident"
}

provider "helm" {
  kubernetes {
    config_path    = "~/.kube/config"
    config_context = "k3d-ai-incident"
  }
}

resource "kubernetes_namespace" "app" {
  metadata {
    name = "app"
    labels = {
      managed-by = "terraform"
      project    = "ai-incident-response"
    }
  }
}

resource "kubernetes_namespace" "monitoring" {
  metadata {
    name = "monitoring"
    labels = {
      managed-by = "terraform"
      project    = "ai-incident-response"
    }
  }
}

resource "kubernetes_namespace" "ai_engine" {
  metadata {
    name = "ai-engine"
    labels = {
      managed-by = "terraform"
      project    = "ai-incident-response"
    }
  }
}

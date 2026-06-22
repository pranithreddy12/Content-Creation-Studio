terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws        = { source = "hashicorp/aws",        version = "~> 5.65" }
    kubernetes = { source = "hashicorp/kubernetes", version = "~> 2.32" }
    helm       = { source = "hashicorp/helm",       version = "~> 2.13" }
  }
  backend "s3" {
    bucket = "studio-tf-state"
    key    = "studio/terraform.tfstate"
    region = "us-east-1"
    encrypt = true
  }
}

provider "aws" {
  region = var.region
  default_tags { tags = { Project = "ai-content-studio", ManagedBy = "terraform" } }
}

variable "region"     { default = "us-east-1" }
variable "env"        { default = "prod" }
variable "vpc_cidr"   { default = "10.10.0.0/16" }
variable "cluster"    { default = "studio-prod" }
variable "domain"     { default = "studio.example.com" }

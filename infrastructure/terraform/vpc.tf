module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.13"

  name = "${var.cluster}-vpc"
  cidr = var.vpc_cidr

  azs             = ["${var.region}a", "${var.region}b", "${var.region}c"]
  public_subnets  = ["10.10.0.0/20", "10.10.16.0/20", "10.10.32.0/20"]
  private_subnets = ["10.10.48.0/20", "10.10.64.0/20", "10.10.80.0/20"]

  enable_nat_gateway   = true
  single_nat_gateway   = false
  enable_dns_hostnames = true
  enable_flow_log      = true
  flow_log_destination_type = "cloud-watch-logs"

  public_subnet_tags  = { "kubernetes.io/role/elb" = "1" }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = "1" }
}

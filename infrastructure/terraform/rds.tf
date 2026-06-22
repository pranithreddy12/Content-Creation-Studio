module "rds" {
  source  = "terraform-aws-modules/rds/aws"
  version = "~> 6.10"

  identifier = "${var.cluster}-postgres"

  engine               = "postgres"
  engine_version       = "16.4"
  family               = "postgres16"
  major_engine_version = "16"
  instance_class       = "db.m6g.large"
  allocated_storage    = 100
  max_allocated_storage = 500

  db_name  = "studio"
  username = "studio"
  manage_master_user_password = true

  multi_az             = true
  publicly_accessible  = false
  vpc_security_group_ids = [aws_security_group.rds.id]
  db_subnet_group_name = aws_db_subnet_group.rds.name

  backup_retention_period = 14
  deletion_protection     = true
  performance_insights_enabled = true
  monitoring_interval = 30
}

resource "aws_db_subnet_group" "rds" {
  name       = "${var.cluster}-rds"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "rds" {
  name   = "${var.cluster}-rds"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    cidr_blocks     = [var.vpc_cidr]
  }
  egress {
    from_port = 0; to_port = 0; protocol = "-1"; cidr_blocks = ["0.0.0.0/0"]
  }
}

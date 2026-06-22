resource "aws_elasticache_subnet_group" "redis" {
  name       = "${var.cluster}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_security_group" "redis" {
  name   = "${var.cluster}-redis"
  vpc_id = module.vpc.vpc_id
  ingress {
    from_port   = 6379; to_port = 6379; protocol = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id          = "${var.cluster}-redis"
  description                   = "Studio Redis cluster"
  engine                        = "redis"
  engine_version                = "7.1"
  node_type                     = "cache.m6g.large"
  num_node_groups               = 3
  replicas_per_node_group       = 1
  automatic_failover_enabled    = true
  multi_az_enabled              = true
  at_rest_encryption_enabled    = true
  transit_encryption_enabled    = true
  subnet_group_name             = aws_elasticache_subnet_group.redis.name
  security_group_ids            = [aws_security_group.redis.id]
  parameter_group_name          = "default.redis7.cluster.on"
}

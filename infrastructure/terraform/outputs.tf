output "cluster_name"       { value = module.eks.cluster_name }
output "cluster_endpoint"   { value = module.eks.cluster_endpoint }
output "vpc_id"             { value = module.vpc.vpc_id }
output "rds_endpoint"       { value = module.rds.db_instance_endpoint }
output "redis_endpoint"     { value = aws_elasticache_replication_group.redis.primary_endpoint_address }
output "media_bucket"       { value = aws_s3_bucket.media.bucket }
output "media_cdn"          { value = aws_cloudfront_distribution.media.domain_name }

resource "aws_s3_bucket" "media" {
  bucket = "${var.cluster}-media"
}

resource "aws_s3_bucket_versioning" "media" {
  bucket = aws_s3_bucket.media.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "AES256" } }
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id
  rule {
    id     = "transition-cold"
    status = "Enabled"
    transition { days = 30; storage_class = "STANDARD_IA" }
    transition { days = 90; storage_class = "GLACIER" }
  }
}

resource "aws_cloudfront_distribution" "media" {
  enabled             = true
  default_root_object = ""
  origin {
    origin_id   = "studio-media-origin"
    domain_name = aws_s3_bucket.media.bucket_regional_domain_name
    s3_origin_config { origin_access_identity = aws_cloudfront_origin_access_identity.media.cloudfront_access_identity_path }
  }
  default_cache_behavior {
    allowed_methods  = ["GET", "HEAD"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "studio-media-origin"
    viewer_protocol_policy = "redirect-to-https"
    forwarded_values { query_string = false; cookies { forward = "none" } }
    min_ttl = 0; default_ttl = 3600; max_ttl = 86400
  }
  restrictions { geo_restriction { restriction_type = "none" } }
  viewer_certificate { cloudfront_default_certificate = true }
}

resource "aws_cloudfront_origin_access_identity" "media" {
  comment = "studio-media-oai"
}

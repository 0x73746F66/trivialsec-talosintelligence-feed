locals {
  aws_master_account_id = 984310022655
  aws_default_region    = "ap-southeast-2"
  python_version        = "python3.10"
  source_file           = "${lower(var.app_env)}-${var.app_name}.zip"
  function_name         = "${lower(var.app_env)}-feed-processor-talos-intelligence"
  tags = {
    ProjectName      = "early-warning-service"
    ProjectLeadEmail = "chris@trivialsec.com"
    CostCenter       = var.app_env != "Prod" ? "randd" : "opex"
    SecurityTags     = "public-data"
    AutomationTool   = "Terraform"
    LUMIGO_TAG       = var.app_env == "Prod" ? "Production" : "Development"
  }
  timeout           = 300
  memory_size       = 256
  retention_in_days = var.app_env == "Prod" ? 30 : 7
  enable_dynatrace  = false # var.app_env == "Prod"
}

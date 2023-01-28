locals {
    aws_master_account_id = 984310022655
    aws_default_region    = "ap-southeast-2"
    python_version        = "python3.9"
    source_file           = "${lower(var.app_env)}-${var.app_name}.zip"
    function_name         = "${lower(var.app_env)}-feed-processor-talos-intelligence"
    timeout               = 300
    memory_size           = 512
    tags                  = {
        ProjectName = "early-warning-service"
        ProjectLeadEmail = "chris@trivialsec.com"
        CostCenter = var.app_env != "Prod" ? "randd" : "opex"
        SecurityTags = "public-data"
        AutomationTool = "Terraform"
    }
}

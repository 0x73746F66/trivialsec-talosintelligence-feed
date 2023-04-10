data "terraform_remote_state" "trivialscan_s3" {
  backend = "s3"
  config = {
    bucket = "stateful-trivialsec"
    key    = "terraform/trivialscan-s3"
    region = "ap-southeast-2"
  }
}
data "terraform_remote_state" "ews_sqs" {
  backend = "s3"
  config = {
    bucket = "stateful-trivialsec"
    key    = "terraform${var.app_env == "Dev" ? "/${lower(var.app_env)}" : ""}/early-warning-service"
    region = "ap-southeast-2"
  }
}

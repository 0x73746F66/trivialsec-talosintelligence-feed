resource "aws_ssm_parameter" "lumigo_token" {
  name      = "/${var.app_env}/${var.app_name}/Lumigo/token"
  type      = "SecureString"
  value     = var.lumigo_token
  tags      = local.tags
  overwrite = true
}

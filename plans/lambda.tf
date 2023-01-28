resource "aws_lambda_function" "feed_processor_talos-intelligence" {
  filename      = "${abspath(path.module)}/${local.source_file}"
  source_code_hash = filebase64sha256("${abspath(path.module)}/${local.source_file}")
  function_name = local.function_name
  role          = aws_iam_role.feed_processor_talos-intelligence_role.arn
  handler       = "app.handler"
  runtime       = local.python_version
  timeout       = local.timeout
  memory_size   = local.memory_size

  environment {
    variables = {
      APP_ENV = var.app_env
      APP_NAME = var.app_name
      LOG_LEVEL = var.log_level
      STORE_BUCKET = "${data.terraform_remote_state.trivialscan_s3.outputs.trivialscan_store_bucket[0]}"
      BUILD_ENV = var.build_env
    }
  }
  lifecycle {
    create_before_destroy = true
  }
  depends_on = [
    aws_iam_role_policy_attachment.policy_attach
  ]
  tags = local.tags
}

resource "aws_cloudwatch_event_rule" "feed_processor_talos-intelligence_schedule" {
    name = "${lower(var.app_env)}_feed_processor_talos-intelligence_schedule"
    description = "Schedule for Lambda Function"
    schedule_expression = var.schedule
}

resource "aws_cloudwatch_event_target" "schedule_lambda" {
    rule = aws_cloudwatch_event_rule.feed_processor_talos-intelligence_schedule.name
    target_id = "${lower(var.app_env)}_feed_processor_talos-intelligence"
    arn = aws_lambda_function.feed_processor_talos-intelligence.arn
}

resource "aws_lambda_permission" "allow_events_bridge_to_run_lambda" {
    statement_id = "${var.app_env}AllowExecutionFromCloudWatch"
    action = "lambda:InvokeFunction"
    function_name = aws_lambda_function.feed_processor_talos-intelligence.function_name
    principal = "events.amazonaws.com"
}

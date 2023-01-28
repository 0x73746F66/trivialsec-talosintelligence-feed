output "feed_processor_talos-intelligence_arn" {
    value = aws_lambda_function.feed_processor_talos-intelligence.arn
}
output "feed_processor_talos-intelligence_role" {
  value = aws_iam_role.feed_processor_talos-intelligence_role.name
}
output "feed_processor_talos-intelligence_role_arn" {
  value = aws_iam_role.feed_processor_talos-intelligence_role.arn
}
output "feed_processor_talos-intelligence_policy_arn" {
  value = aws_iam_policy.feed_processor_talos-intelligence_policy.arn
}

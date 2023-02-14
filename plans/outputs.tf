output "feed_processor_talos_arn" {
    value = aws_lambda_function.feed_processor_talos.arn
}
output "feed_processor_talos_role" {
  value = aws_iam_role.feed_processor_talos_role.name
}
output "feed_processor_talos_role_arn" {
  value = aws_iam_role.feed_processor_talos_role.arn
}
output "feed_processor_talos_policy_arn" {
  value = aws_iam_policy.feed_processor_talos_policy.arn
}

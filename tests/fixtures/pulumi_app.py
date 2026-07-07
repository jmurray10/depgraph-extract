"""Minimal Pulumi fixture used for parser tests (parsed via AST, never run)."""

import pulumi
import pulumi_aws as aws


bucket = aws.s3.Bucket("my-bucket")

bucket_policy = aws.s3.BucketPolicy(
    "my-bucket-policy",
    bucket=bucket,
)

queue = aws.sqs.Queue("my-queue")

notif = aws.s3.BucketNotification(
    "my-bucket-notif",
    bucket=bucket,
    queues=[queue],
)

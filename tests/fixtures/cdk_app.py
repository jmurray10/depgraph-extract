"""Minimal AWS CDK fixture used for parser tests (parsed via AST, never run)."""

from aws_cdk import Stack, aws_lambda, aws_s3, aws_iam, aws_ec2
from constructs import Construct


class MyStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        my_vpc = aws_ec2.Vpc(self, "MyVpc", max_azs=2)
        my_role = aws_iam.Role(
            self,
            "MyRole",
            assumed_by=aws_iam.ServicePrincipal("lambda.amazonaws.com"),
        )
        my_bucket = aws_s3.Bucket(self, "MyBucket", versioned=True)

        my_fn = aws_lambda.Function(
            self,
            "MyFn",
            runtime=aws_lambda.Runtime.PYTHON_3_11,
            handler="index.handler",
            role=my_role,
            vpc=my_vpc,
        )

        my_bucket.grant_read(my_fn)

"""Tests for the IaC extractors: CloudFormation, AWS CDK, Pulumi.

Pure parsers — they never import the IaC framework. Tests run without
pip install aws-cdk-lib / pulumi etc.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest

from depgraph_extract import extract


FIXTURES = Path(__file__).parent / "fixtures"


# ---------- CloudFormation ----------------------------------------------

class TestCloudFormation:
    def test_yaml_template_with_intrinsics_and_dependson(self):
        edges = extract.from_cloudformation(str(FIXTURES / "stack.yaml"))
        edges_set = {tuple(e) for e in edges}
        # Implicit Ref
        assert ("MySubnet", "MyVpc") in edges_set
        # Explicit DependsOn
        assert ("MyFunction", "MyBucket") in edges_set
        # Fn::GetAtt
        assert ("MyFunction", "MyRole") in edges_set
        # Fn::Ref inside nested VpcConfig
        assert ("MyFunction", "MySubnet") in edges_set

    def test_json_template(self, tmp_path):
        import json
        template = {
            "Resources": {
                "Bucket": {"Type": "AWS::S3::Bucket"},
                "Topic": {
                    "Type": "AWS::SNS::Topic",
                    "Properties": {"TopicName": {"Ref": "Bucket"}},
                    "DependsOn": "Bucket",
                },
            }
        }
        f = tmp_path / "stack.json"
        f.write_text(json.dumps(template))
        edges = extract.from_cloudformation(str(f))
        # Both DependsOn and Ref produce the same edge — dedupe collapses.
        assert {tuple(e) for e in edges} == {("Topic", "Bucket")}

    def test_directory_of_templates_merges(self, tmp_path):
        import json
        (tmp_path / "a.json").write_text(json.dumps({
            "Resources": {
                "Bucket": {"Type": "AWS::S3::Bucket"},
                "Fn": {"Type": "AWS::Lambda::Function", "DependsOn": "Bucket"},
            }
        }))
        (tmp_path / "b.json").write_text(json.dumps({
            "Resources": {
                "Q": {"Type": "AWS::SQS::Queue"},
                "Sub": {"Type": "AWS::SNS::Subscription", "DependsOn": "Q"},
            }
        }))
        edges = extract.from_cloudformation(str(tmp_path))
        edges_set = {tuple(e) for e in edges}
        assert ("Fn", "Bucket") in edges_set
        assert ("Sub", "Q") in edges_set

    def test_unknown_ref_target_dropped(self, tmp_path):
        import json
        (tmp_path / "stack.json").write_text(json.dumps({
            "Resources": {
                "Bucket": {
                    "Type": "AWS::S3::Bucket",
                    # !Ref to a name that isn't a resource — must not produce an edge
                    "Properties": {"BucketName": {"Ref": "ExternalParameter"}},
                },
            }
        }))
        edges = extract.from_cloudformation(str(tmp_path / "stack.json"))
        assert edges == []


# ---------- AWS CDK -----------------------------------------------------

class TestAwsCdk:
    def test_extracts_kwargs_referencing_construct_vars(self):
        edges = extract.from_aws_cdk(str(FIXTURES / "cdk_app.py"))
        edges_set = {tuple(e) for e in edges}
        assert ("my_fn", "my_role") in edges_set
        assert ("my_fn", "my_vpc") in edges_set

    def test_ignores_unrelated_kwargs(self):
        # `versioned=True`, `runtime=...`, `handler="..."` etc. don't reference
        # construct vars and shouldn't produce edges.
        edges = extract.from_aws_cdk(str(FIXTURES / "cdk_app.py"))
        edges_set = {tuple(e) for e in edges}
        # Sanity: only the role + vpc edges should appear (no spurious targets).
        assert all(t in {"my_role", "my_vpc"} for _s, t in edges_set)


# ---------- Pulumi ------------------------------------------------------

class TestPulumi:
    def test_extracts_kwarg_and_list_kwarg_references(self):
        edges = extract.from_pulumi(str(FIXTURES / "pulumi_app.py"))
        edges_set = {tuple(e) for e in edges}
        assert ("bucket_policy", "bucket") in edges_set
        assert ("notif", "bucket") in edges_set
        assert ("notif", "queue") in edges_set


# ---------- from_directory auto-detect -----------------------------------

class TestFromDirectoryAutoDetect:
    def test_picks_up_cloudformation_yaml(self, tmp_path):
        shutil.copy(FIXTURES / "stack.yaml", tmp_path / "stack.yaml")
        edges, sources = extract.from_directory(str(tmp_path))
        assert "cloudformation" in sources
        assert ("MySubnet", "MyVpc") in {tuple(e) for e in edges}

    def test_skips_yaml_without_resources_section(self, tmp_path):
        # A plain non-CFN yaml file must not trip the cfn detector.
        (tmp_path / "config.yaml").write_text(textwrap.dedent("""\
            name: my-app
            version: 1.0
            settings:
              debug: true
        """))
        _edges, sources = extract.from_directory(str(tmp_path))
        assert "cloudformation" not in sources

    def test_picks_up_cdk_python_file(self, tmp_path):
        shutil.copy(FIXTURES / "cdk_app.py", tmp_path / "stack.py")
        edges, sources = extract.from_directory(str(tmp_path))
        assert "aws-cdk" in sources
        assert ("my_fn", "my_role") in {tuple(e) for e in edges}

    def test_picks_up_pulumi_python_file(self, tmp_path):
        shutil.copy(FIXTURES / "pulumi_app.py", tmp_path / "__main__.py")
        edges, sources = extract.from_directory(str(tmp_path))
        assert "pulumi" in sources
        assert ("notif", "bucket") in {tuple(e) for e in edges}

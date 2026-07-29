"""infra/cloudformation/ec2-alb-stack.yaml에 대한 정적 검증 - 실제 AWS 배포 없이도
YAML 문법 오류, 존재하지 않는 논리 ID 참조(오타로 인한 흔한 실수), 필수 리소스 누락을
CI에서 잡아내기 위한 회귀 테스트. 실제 AWS 계정/자격증명은 필요 없음(순수 정적 분석)."""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "infra" / "cloudformation" / "ec2-alb-stack.yaml"


@pytest.fixture(scope="module")
def template_text():
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template(template_text):
    return yaml.safe_load(template_text)


def test_template_file_exists():
    assert TEMPLATE_PATH.exists()


def test_template_is_valid_yaml_with_expected_top_level_sections(template):
    assert template["AWSTemplateFormatVersion"] == "2010-09-09"
    for key in ("Parameters", "Resources", "Outputs"):
        assert key in template
        assert len(template[key]) > 0


def test_required_parameters_have_no_silent_defaults_for_domain_and_zone(template):
    """DomainName/HostedZoneId는 실수로 잘못된 도메인에 배포되는 사고를 막기 위해 기본값 없이
    배포자가 명시적으로 입력해야 한다."""
    assert "Default" not in template["Parameters"]["DomainName"]
    assert "Default" not in template["Parameters"]["HostedZoneId"]


def test_no_duplicate_resource_logical_ids(template_text):
    in_resources = False
    seen: dict[str, int] = {}
    for line in template_text.split("\n"):
        if re.match(r"^Resources:\s*$", line):
            in_resources = True
            continue
        if in_resources and re.match(r"^\S", line):
            in_resources = False
        if in_resources:
            m = re.match(r"^  (\w+):\s*$", line)
            if m:
                seen[m.group(1)] = seen.get(m.group(1), 0) + 1
    duplicates = [name for name, count in seen.items() if count > 1]
    assert duplicates == []


def test_all_ref_and_getatt_targets_resolve_to_known_ids(template, template_text):
    known_ids = set(template["Parameters"].keys()) | set(template["Resources"].keys())
    refs = set(re.findall(r'"Ref":\s*"([A-Za-z0-9]+)"', template_text))
    getatts = set(re.findall(r'"Fn::GetAtt":\s*\[\s*"([A-Za-z0-9]+)"', template_text))
    dangling = sorted(r for r in (refs | getatts) if r not in known_ids and r != "AWS::NoValue")
    assert dangling == []


@pytest.mark.parametrize("logical_id,expected_type", [
    ("Vpc", "AWS::EC2::VPC"),
    ("Ec2Instance", "AWS::EC2::Instance"),
    ("Ec2Role", "AWS::IAM::Role"),
    ("AlbSecurityGroup", "AWS::EC2::SecurityGroup"),
    ("AppSecurityGroup", "AWS::EC2::SecurityGroup"),
    ("Alb", "AWS::ElasticLoadBalancingV2::LoadBalancer"),
    ("TargetGroup", "AWS::ElasticLoadBalancingV2::TargetGroup"),
    ("AcmCertificate", "AWS::CertificateManager::Certificate"),
    ("EcrRepository", "AWS::ECR::Repository"),
    ("AppSecret", "AWS::SecretsManager::Secret"),
    ("DnsRecord", "AWS::Route53::RecordSet"),
    ("NatGateway", "AWS::EC2::NatGateway"),
])
def test_expected_resource_exists_with_correct_type(template, logical_id, expected_type):
    assert template["Resources"][logical_id]["Type"] == expected_type


def test_ec2_instance_is_in_a_private_subnet_with_no_ssh_ingress(template):
    """docs/AWS_배포_운영_매뉴얼.md 7장 요구사항 - EC2는 프라이빗 서브넷에 있고 22번 포트를
    열지 않아야 한다(SSM Session Manager 전용 접속)."""
    ec2 = template["Resources"]["Ec2Instance"]["Properties"]
    assert ec2["SubnetId"] == {"Ref": "PrivateSubnetA"}

    app_sg = template["Resources"]["AppSecurityGroup"]["Properties"]
    ingress_ports = {rule["FromPort"] for rule in app_sg.get("SecurityGroupIngress", [])}
    assert 22 not in ingress_ports


def test_app_security_group_only_allows_ingress_from_alb_security_group(template):
    app_sg = template["Resources"]["AppSecurityGroup"]["Properties"]
    ingress_rules = app_sg["SecurityGroupIngress"]
    assert len(ingress_rules) == 1
    assert ingress_rules[0]["FromPort"] == 8000
    assert ingress_rules[0]["SourceSecurityGroupId"] == {"Ref": "AlbSecurityGroup"}


def test_alb_security_group_allows_public_http_and_https_only(template):
    alb_sg = template["Resources"]["AlbSecurityGroup"]["Properties"]
    ports = sorted(rule["FromPort"] for rule in alb_sg["SecurityGroupIngress"])
    assert ports == [80, 443]
    for rule in alb_sg["SecurityGroupIngress"]:
        assert rule["CidrIp"] == "0.0.0.0/0"


def test_http_listener_redirects_to_https(template):
    action = template["Resources"]["HttpListener"]["Properties"]["DefaultActions"][0]
    assert action["Type"] == "redirect"
    assert action["RedirectConfig"]["Protocol"] == "HTTPS"
    assert action["RedirectConfig"]["Port"] == "443"


def test_target_group_health_check_matches_app_health_endpoint(template):
    tg = template["Resources"]["TargetGroup"]["Properties"]
    assert tg["HealthCheckPath"] == "/health"
    assert tg["Port"] == 8000
    assert tg["Matcher"]["HttpCode"] == "200"


def test_app_secret_placeholder_contains_no_real_looking_secret_values(template):
    """실제 키/토큰 값이 실수로 커밋되는 사고를 막기 위한 회귀 테스트 - 전부 빈 문자열이어야 함."""
    secret_string = template["Resources"]["AppSecret"]["Properties"]["SecretString"]
    import json

    parsed = json.loads(secret_string)
    assert all(value == "" for value in parsed.values())


def test_ec2_role_has_ssm_managed_policy_for_sshless_access(template):
    role = template["Resources"]["Ec2Role"]["Properties"]
    assert "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" in role["ManagedPolicyArns"]

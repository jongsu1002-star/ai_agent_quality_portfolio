"""infra/cloudformation/ec2-freetier-stack.yaml에 대한 정적 검증 - 실제 AWS 계정 없이도
YAML 문법 오류, 존재하지 않는 논리 ID 참조, 비용 안전장치(예산 알림)와 핵심 보안 규칙
(SSH 미개방 등)을 CI에서 잡아낸다."""

import json
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "infra" / "cloudformation" / "ec2-freetier-stack.yaml"


@pytest.fixture(scope="module")
def template_text():
    return TEMPLATE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def template(template_text):
    return yaml.safe_load(template_text)


def test_template_file_exists():
    assert TEMPLATE_PATH.exists()


def test_template_description_is_within_aws_1024_char_limit(template):
    """AWS CloudFormation의 최상위 Description 필드는 1024자 제한이 있다(cfn-lint는 이
    제약을 검사하지 않고 실제 aws cloudformation validate-template API 호출로만 실측
    확인됨) - 회귀 테스트로 고정."""
    assert len(template["Description"]) <= 1024


def test_template_is_valid_yaml_with_expected_top_level_sections(template):
    assert template["AWSTemplateFormatVersion"] == "2010-09-09"
    for key in ("Parameters", "Resources", "Outputs"):
        assert key in template
        assert len(template[key]) > 0


def test_no_nat_gateway_alb_route53_or_secrets_manager(template_text):
    """이 템플릿의 핵심 목적 - 비용이 드는 리소스(NAT Gateway/ALB/Route 53 Hosted Zone/
    Secrets Manager)를 절대 만들지 않아야 한다(ec2-alb-stack.yaml과 달리)."""
    for forbidden in ("AWS::EC2::NatGateway", "AWS::ElasticLoadBalancingV2", "AWS::Route53::", "AWS::SecretsManager::"):
        assert forbidden not in template_text


def test_instance_type_only_allows_free_tier_eligible_types(template):
    param = template["Parameters"]["InstanceType"]
    assert set(param["AllowedValues"]) == {"t2.micro", "t3.micro"}
    assert param["Default"] in param["AllowedValues"]


def test_root_volume_size_capped_at_free_tier_ebs_allowance(template):
    param = template["Parameters"]["RootVolumeSizeGiB"]
    assert param["MaxValue"] <= 30


def test_security_group_has_no_ssh_ingress(template):
    sg_props = template["Resources"]["AppSecurityGroup"]["Properties"]
    ports = {rule["FromPort"] for rule in sg_props["SecurityGroupIngress"]}
    assert 22 not in ports


def test_ec2_role_has_ssm_managed_policy_for_sshless_access(template):
    role = template["Resources"]["Ec2Role"]["Properties"]
    assert "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" in role["ManagedPolicyArns"]


def test_security_group_descriptions_are_ascii_only(template):
    """EC2 API는 보안그룹의 GroupDescription뿐 아니라 개별 인바운드/아웃바운드 규칙의
    Description도 ASCII 문자만 허용한다 - 실제 이 스택을 AWS에 생성하려던 시도에서
    "Invalid rule description" 오류로 직접 실패를 겪고 발견함(cfn-lint/validate-template
    둘 다 이 런타임 제약은 잡아내지 못했음). 회귀 방지용 - Parameters/Outputs Description은
    이 제약 대상이 아니므로 검사하지 않는다."""
    problems = []
    for logical_id, res in template["Resources"].items():
        rtype = res.get("Type", "")
        props = res.get("Properties", {})
        if rtype == "AWS::EC2::SecurityGroup":
            group_desc = props.get("GroupDescription", "")
            if any(ord(c) > 127 for c in group_desc):
                problems.append(f"{logical_id}.GroupDescription")
            for kind in ("SecurityGroupIngress", "SecurityGroupEgress"):
                for rule in props.get(kind, []):
                    if any(ord(c) > 127 for c in rule.get("Description", "")):
                        problems.append(f"{logical_id}.{kind}[].Description")
        elif rtype in ("AWS::EC2::SecurityGroupIngress", "AWS::EC2::SecurityGroupEgress"):
            if any(ord(c) > 127 for c in props.get("Description", "")):
                problems.append(f"{logical_id}.Description")
    assert problems == []


def test_budget_alert_configured_with_email_subscriber(template):
    """비용 안전장치 - 예산 초과 시 실제로 이메일 알림이 가도록 구독자가 반드시 있어야 함."""
    budget = template["Resources"]["CostBudget"]["Properties"]["Budget"]
    assert budget["BudgetType"] == "COST"
    assert budget["TimeUnit"] == "MONTHLY"
    notifications = template["Resources"]["CostBudget"]["Properties"]["NotificationsWithSubscribers"]
    assert len(notifications) >= 1
    for notif in notifications:
        subscribers = notif["Subscribers"]
        assert any(s["SubscriptionType"] == "EMAIL" for s in subscribers)


def test_all_ref_and_getatt_targets_resolve_to_known_ids(template, template_text):
    import re
    known_ids = set(template["Parameters"].keys()) | set(template["Resources"].keys())
    refs = set(re.findall(r'"Ref":\s*"([A-Za-z0-9]+)"', template_text))
    getatts = set(re.findall(r'"Fn::GetAtt":\s*\[\s*"([A-Za-z0-9]+)"', template_text))
    dangling = sorted(r for r in (refs | getatts) if r not in known_ids and r != "AWS::NoValue")
    assert dangling == []


def test_no_duplicate_resource_logical_ids(template_text):
    import re
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

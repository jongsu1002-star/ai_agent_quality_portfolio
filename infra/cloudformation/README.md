# CloudFormation - `ec2-alb-stack.yaml`

`docs/AWS_배포_운영_매뉴얼.md`에 문서화된 아키텍처(VPC 2개 AZ + ALB/ACM/Route 53 +
프라이빗 서브넷 EC2 + ECR + Secrets Manager + SSM 전용 접속)를 그대로 코드화한
CloudFormation 템플릿입니다. **네트워크/컴퓨트/보안 경계까지만** 이 템플릿이 만들고,
애플리케이션 자체의 배포(이미지 빌드·ECR 푸시·`.env`/Secrets 값 입력·`docker compose up`)는
사람이 직접 확인해야 하는 단계라 매뉴얼의 4~10장 수동 절차를 그대로 따릅니다.

## 사전 준비

- AWS CLI v2, `aws configure` 완료(매뉴얼 3장과 동일)
- 서비스에 쓸 도메인이 이미 Route 53 Hosted Zone으로 관리되고 있어야 함(신규 Hosted Zone
  생성은 이 템플릿 범위 밖 - 도메인 소유권 이전 같은 사람의 판단이 필요한 단계이기 때문)

## 배포

```bash
aws cloudformation deploy \
  --template-file infra/cloudformation/ec2-alb-stack.yaml \
  --stack-name qa-platform \
  --parameter-overrides \
    DomainName=qa.example.com \
    HostedZoneId=Z0123456789ABCDEFGHIJ \
  --capabilities CAPABILITY_IAM
```

ACM 인증서 DNS 검증이 자동으로 이뤄지므로(Route 53 Hosted Zone에 검증 레코드를 CloudFormation이
직접 생성) 별도로 콘솔에서 인증서를 승인할 필요가 없지만, 검증 완료까지 수 분이 걸릴 수
있습니다.

## 배포 후 해야 할 일 (이 템플릿이 대신 하지 않는 것)

1. **Secrets Manager 값 채우기**: 스택이 `qa-platform/prod`라는 이름으로 시크릿을 만들지만
   내용은 전부 빈 문자열입니다. 실제 값(OPENAI_API_KEY 등)은 콘솔 또는
   `aws secretsmanager put-secret-value`로 배포 담당자가 직접 입력하세요(이 템플릿/Git
   저장소 어디에도 실제 키 값을 넣지 않습니다).
2. **이미지 빌드·ECR 푸시**: 매뉴얼 4~5장 그대로(`docker build` → `docker push`).
3. **EC2 접속 및 compose 기동**: SSH가 아니라 SSM Session Manager로 접속합니다
   (`aws ssm start-session --target <Ec2InstanceId 출력값>`) - 보안 그룹에 22번 포트 자체가
   없습니다. 접속 후 매뉴얼 10장의 `/opt/qa-platform/compose.yml`을 작성하고
   `docker compose up -d`.
4. **배포 검증**: `python scripts/verify_deployment.py --base-url https://qa.example.com`으로
   git_sha/헬스체크 확인.

## 이 템플릿이 아직 다루지 않는 것 (알려진 한계)

- **EBS 자동 스냅샷(DLM/AWS Backup)**: 매뉴얼의 리소스 목록에는 있으나 이 템플릿에는 없음 -
  `scripts/backup_data.py`(P1)가 애플리케이션 레벨 백업은 담당하지만, 인스턴스 자체가
  통째로 사라지는 재해에 대비하려면 별도로 DLM 정책을 추가하는 것을 권장합니다.
- **CloudWatch 경보/대시보드**: 로그 그룹만 생성하고 실제 CloudWatch Agent 설치·경보 설정은
  포함하지 않습니다(매뉴얼 15장 참고, 수동 설정 필요).
- **2번째 EC2 인스턴스를 통한 고가용성**: 현재는 단일 인스턴스 전제(이 프로젝트 전체의
  "SQLite 1개, 워커 1개" 설계와 일치) - `PrivateSubnetB`는 나중에 인스턴스를 추가할 여지만
  남겨둔 것으로, 지금은 아무것도 그 안에서 실행되지 않습니다.

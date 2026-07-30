# CloudFormation 템플릿 2종 - 비용 vs 기능으로 선택

| | `ec2-freetier-stack.yaml` | `ec2-alb-stack.yaml` |
|---|---|---|
| 예상 월 비용 | 사실상 $0(프리티어/프로모션 크레딧 소진 전까지) | $50 이상(NAT Gateway+ALB만으로) |
| 구성 | 기본 VPC + EC2 1대(퍼블릭 IP 직접 노출) | 신규 VPC(2 AZ) + ALB + NAT Gateway + Route 53 + ACM |
| HTTPS | 없음(직접 `:8000` 접속, 필요시 수동으로 Nginx+Let's Encrypt 추가 가능) | ALB가 관리형으로 종료 |
| 도메인 | 불필요(IP로 접속) | Route 53 Hosted Zone 필수 |
| 비용 안전장치 | AWS Budgets 이메일 알림 내장 | 없음 |
| 권장 대상 | 개인/소규모 검증, 비용 최소화가 우선 | 실서비스, 커스텀 도메인·HTTPS·다중 AZ가 필요할 때 |

둘 다 **네트워크/컴퓨트/보안 경계까지만** 만들고, 애플리케이션 자체의 배포(이미지
빌드·`.env` 값 입력·`docker compose up`)는 사람이 직접 확인해야 하는 단계라 자동화하지
않습니다.

## `ec2-freetier-stack.yaml` (비용 최소화 - 기본 권장)

```bash
aws cloudformation validate-template \
  --template-body file://infra/cloudformation/ec2-freetier-stack.yaml \
  --region ap-northeast-2

aws cloudformation create-change-set \
  --stack-name qa-platform-freetier \
  --change-set-name initial-create \
  --change-set-type CREATE \
  --template-body file://infra/cloudformation/ec2-freetier-stack.yaml \
  --capabilities CAPABILITY_IAM \
  --region ap-northeast-2 \
  --parameters \
    ParameterKey=VpcId,ParameterValue=<기본 VPC ID> \
    ParameterKey=PublicSubnetId,ParameterValue=<퍼블릭 서브넷 ID> \
    ParameterKey=BudgetAlertEmail,ParameterValue=<알림받을 이메일>

# change set 내용(추가/변경/삭제될 리소스)을 반드시 검토한 뒤에만 실행
aws cloudformation describe-change-set --stack-name qa-platform-freetier --change-set-name initial-create --region ap-northeast-2
aws cloudformation execute-change-set --stack-name qa-platform-freetier --change-set-name initial-create --region ap-northeast-2
```

배포 후:
1. `aws ssm start-session --target <Ec2InstanceId 출력값>`으로 접속(SSH 없음)
2. `git clone` 또는 파일 업로드로 소스를 `/opt/qa-platform`에 올리고, 실제 `.env`(API 키 등)를
   직접 작성 - Secrets Manager를 안 쓰므로(비용 절감) 이 파일이 유일한 비밀값 저장소이며
   `chmod 600`으로 권한을 제한할 것.
3. `docker compose up -d` (기존 `docker-compose.yml` 그대로 - 3000/9090은 여전히
   `127.0.0.1`에만 바인딩되므로 `/grafana-proxy`, `/prometheus-proxy`로만 접근)
4. `python scripts/verify_deployment.py --base-url http://<Outputs.PublicIp>:8000`으로 확인

**비용 관련 주의사항**:
- `AWS::Budgets::Budget`이 월 비용이 `BudgetLimitUsd`(기본 $5)의 80%/100%를 넘으면
  이메일로 알려주지만, 이는 사후 알림이지 사전 차단이 아닙니다.
- EIP는 "실행 중인 인스턴스에 연결된 상태"에서만 무료 - 인스턴스를 **중지(stop)**한 채로
  두면 그 순간부터 EIP 자체에 과금되니, 안 쓸 때는 스택을 통째로 삭제(`aws cloudformation
  delete-stack`)하거나 EIP를 명시적으로 해제할 것.
- 2024년 7월 이후 생성된 신규 계정은 "12개월 상시무료"가 아니라 "6개월 $200 크레딧"
  방식일 수 있음 - 크레딧 소진 후에는 t2.micro/t3.micro도 실비용이 청구됨.

## `ec2-alb-stack.yaml` (실서비스용, 비용 발생)

`docs/AWS_배포_운영_매뉴얼.md`에 문서화된 아키텍처(VPC 2개 AZ + ALB/ACM/Route 53 +
프라이빗 서브넷 EC2 + ECR + Secrets Manager + SSM 전용 접속)를 그대로 코드화한
CloudFormation 템플릿입니다.

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

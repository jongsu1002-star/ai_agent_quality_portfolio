# AI Agent Quality Platform AWS 배포·운영 매뉴얼

작성 기준일: 2026-07-27  
대상 리전 예시: 서울(`ap-northeast-2`)  
대상 시스템: FastAPI + Docker + SQLite + 로컬 리포트 + Prometheus/Grafana

## 1. 목적과 권장 배포 방식

현재 시스템을 코드 변경 없이 AWS에서 서비스하려면 다음 구성이 가장 현실적이다.

```text
사용자
  ↓ HTTPS 443
Route 53(도메인)
  ↓
Application Load Balancer + ACM 인증서
  ↓ HTTP 8000
EC2 1대(Private Subnet 권장)
  ├─ qa-platform 컨테이너
  ├─ Prometheus 컨테이너(외부 비공개)
  ├─ Grafana 컨테이너(외부 비공개)
  └─ 암호화 EBS
      ├─ /opt/qa-platform/data
      └─ /opt/qa-platform/reports
```

현재 애플리케이션은 다음 상태를 로컬에 저장하므로 **EC2 한 대, 애플리케이션 컨테이너 한 개**로 운영해야 한다.

- `data/users.db`, `data/board.db`, `data/monitoring_addon.db`, `data/sessions.db`, `data/ip_allowlist.db`: SQLite
- `reports/`: 업로드, 설정, 실행 결과, 감사 증적
- 로그인 세션: `data/sessions.db`(SQLite)에 영속화되어 컨테이너 재시작에도 유지됨(`SESSION_TTL_SECONDS`로 서버 측 만료 관리) - 실행 상태(QA 파이프라인/VOC 분석 진행 상황)는 여전히 프로세스 메모리

따라서 Auto Scaling, ECS 다중 Task, Uvicorn 다중 Worker를 바로 적용하면 실행 상태 불일치, SQLite 충돌 또는 파일 불일치가 발생할 수 있다(세션 자체는 SQLite 영속화 이후 다중 워커에서도 공유는 가능하나, 실행 상태 레지스트리가 아직 프로세스 메모리라 여전히 단일 워커 전제). 이 매뉴얼의 1단계는 사내 서비스·PoC·초기 운영에 적합하다. 인터넷 공개 또는 무중단 고가용성 운영은 14장의 구조 개선 후 진행한다.

> **자동화(IaC)**: 아래 2~8장·11장에서 수동으로 만드는 리소스(VPC, ALB, ACM, Route 53, 보안 그룹, IAM 역할, EC2, ECR, Secrets Manager)는 `infra/cloudformation/ec2-alb-stack.yaml`로 대신 한 번에 생성할 수 있다(사용법은 `infra/cloudformation/README.md` 참고). 이미지 빌드·ECR 푸시·Secrets 값 입력·`docker compose up`처럼 사람의 판단이 필요한 단계는 이 매뉴얼의 절차를 그대로 따른다.

## 2. AWS 리소스 목록

| 구분 | 권장값 | 용도 |
|---|---|---|
| VPC | 2개 AZ, Public/Private Subnet | ALB와 EC2 분리 |
| EC2 | Amazon Linux 2023, `t3.medium` 이상 | 앱과 모니터링 실행 |
| EBS | gp3 30~50 GiB, 암호화 | DB와 리포트 영구 저장 |
| ECR | Private Repository 1개 | 애플리케이션 이미지 저장 |
| ALB | Internet-facing | HTTPS 종료와 헬스 체크 |
| ACM | 도메인 인증서 | TLS 인증서 |
| Route 53 | 기존 또는 신규 Hosted Zone | DNS |
| Secrets Manager | 운영 비밀값 | LLM 키, Webhook, Jira 토큰 |
| CloudWatch | 로그, 경보 | 운영 관제 |
| SSM Session Manager | EC2 접속 | 22번 포트 없는 관리 |
| DLM 또는 AWS Backup | 매일 스냅샷 | EBS 백업 |

비용은 인스턴스 크기, ALB 사용 시간, EBS 용량, 로그량, NAT Gateway 유무에 따라 달라진다. 구축 직전에 AWS Pricing Calculator로 산정한다.

## 3. 사전 준비

운영 담당 PC에 다음 도구를 설치한다.

- Git
- Docker Desktop 또는 Docker Engine
- AWS CLI v2
- 도메인(예: `qa.example.com`)
- AWS 계정과 `ap-northeast-2` 사용 권한

AWS CLI를 설정한다.

```bash
aws configure
aws sts get-caller-identity
```

리전과 계정 번호를 셸 변수로 지정한다. 아래 명령은 Bash 기준이다.

```bash
export AWS_REGION=ap-northeast-2
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR_REPOSITORY=ai-agent-quality-platform
export IMAGE_TAG=$(git rev-parse --short HEAD)
```

PowerShell에서는 다음과 같이 지정한다.

```powershell
$env:AWS_REGION = "ap-northeast-2"
$env:AWS_ACCOUNT_ID = aws sts get-caller-identity --query Account --output text
$env:ECR_REPOSITORY = "ai-agent-quality-platform"
$env:IMAGE_TAG = git rev-parse --short HEAD
```

## 4. 배포 전 필수 점검

프로젝트 루트에서 테스트와 이미지 빌드를 수행한다.

```bash
pytest -q
docker build --build-arg GIT_SHA="$IMAGE_TAG" -t "$ECR_REPOSITORY:$IMAGE_TAG" .
docker run --rm -d --name qa-platform-check -p 18000:8000 "$ECR_REPOSITORY:$IMAGE_TAG"
curl --fail http://localhost:18000/health
docker stop qa-platform-check
```

다음을 확인한다.

- `.env`가 Git 및 Docker 이미지에 포함되지 않았는가
- 운영용 `ADMIN_SETUP_CODE`를 충분히 긴 임의 문자열로 생성했는가
- OpenAI/Anthropic 키, Jira 토큰, Webhook URL을 소스에 기록하지 않았는가
- 실제 데이터가 필요한 경우 기존 `data/`, `reports/`를 별도 백업했는가
- 운영 서버에서 `pytest`를 실행하지 않는가. 테스트 산출물이 운영 `reports/`에 섞일 수 있다.

주의: 현재 `.dockerignore`가 `.env`를 제외하는지 반드시 확인한다. 아래 명령 결과에 `.env`가 나타나면 배포를 중단한다.

```bash
docker run --rm "$ECR_REPOSITORY:$IMAGE_TAG" sh -c 'test ! -f /app/.env'
```

## 5. ECR 생성과 이미지 업로드

ECR 저장소를 한 번만 생성한다.

```bash
aws ecr create-repository \
  --repository-name "$ECR_REPOSITORY" \
  --image-scanning-configuration scanOnPush=true \
  --encryption-configuration encryptionType=AES256 \
  --region "$AWS_REGION"
```

로그인 후 이미지에 태그를 붙여 업로드한다.

```bash
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin \
    "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

docker tag "$ECR_REPOSITORY:$IMAGE_TAG" \
  "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:$IMAGE_TAG"

docker push \
  "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:$IMAGE_TAG"
```

`latest`만 사용하지 말고 Git SHA처럼 되돌릴 수 있는 불변 태그를 사용한다.

## 6. IAM 역할 생성

EC2 인스턴스 역할에 다음 권한을 부여한다.

- `AmazonSSMManagedInstanceCore`
- ECR 이미지 Pull에 필요한 최소 권한
- CloudWatch Logs 기록에 필요한 최소 권한
- 사용할 Secrets Manager Secret의 `secretsmanager:GetSecretValue`
- Customer managed KMS 키를 쓴다면 해당 키의 `kms:Decrypt`

편의를 위해 광범위한 관리자 정책을 붙이지 않는다. 애플리케이션 컨테이너에는 AWS 장기 Access Key를 저장하지 않는다.

## 7. 네트워크와 보안 그룹

### 7.1 권장 네트워크

- ALB: Public Subnet 2개 이상
- EC2: Private Subnet
- EC2의 외부 패키지 및 ECR 접근: NAT Gateway 또는 ECR/S3/SSM VPC Endpoint
- 관리 접속: SSM Session Manager

비용을 줄이는 초기 PoC에서는 EC2를 Public Subnet에 둘 수 있지만, Public IPv4를 직접 서비스 주소로 사용하지 않고 ALB만 공개한다.

### 7.2 보안 그룹

`sg-alb`:

- Inbound TCP 443: `0.0.0.0/0`, 필요 시 `::/0`
- Inbound TCP 80: HTTPS 리다이렉트용
- Outbound TCP 8000: `sg-app`

`sg-app`:

- Inbound TCP 8000: Source `sg-alb`만
- Inbound 22: 없음
- Outbound 443: ECR, Secrets Manager, LLM API, Jira, Webhook 호출

다음 포트는 인터넷에 공개하지 않는다.

- 8000: FastAPI 직접 접근
- 9090: Prometheus
- 3000: Grafana
- 22: SSH

## 8. EC2 생성과 초기 설정

Amazon Linux 2023, `t3.medium` 이상, 암호화 gp3 EBS로 인스턴스를 생성하고 6장의 IAM 역할과 `sg-app`을 연결한다.

SSM으로 접속한 후 Docker를 설치한다.

```bash
sudo dnf update -y
sudo dnf install -y docker git
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user
```

세션을 종료하고 다시 접속한 뒤 확인한다.

```bash
docker version
docker compose version
```

Compose 플러그인이 없다면 현재 Amazon Linux 2023용 공식 설치 방법에 따라 Docker Compose v2 플러그인을 추가한다. 임의의 오래된 `docker-compose` v1 바이너리는 사용하지 않는다.

운영 디렉터리를 만든다.

```bash
sudo mkdir -p /opt/qa-platform/{data,reports,infra}
sudo chown -R ec2-user:ec2-user /opt/qa-platform
chmod 700 /opt/qa-platform
```

별도 EBS 데이터 볼륨을 사용한다면 `/opt/qa-platform`에 마운트하고 `/etc/fstab`에 UUID를 등록한다. 포맷 전에 대상 장치명을 반드시 확인한다.

## 9. 운영 환경 변수

Secrets Manager에 `qa-platform/prod` 이름으로 운영 비밀값을 저장한다. 예:

```json
{
  "ADMIN_SETUP_CODE": "충분히-긴-무작위-문자열",
  "OPENAI_API_KEY": "",
  "ANTHROPIC_API_KEY": "",
  "SLACK_WEBHOOK_URL": "",
  "DISCORD_WEBHOOK_URL": "",
  "TEAMS_WEBHOOK_URL": "",
  "JIRA_BASE_URL": "",
  "JIRA_EMAIL": "",
  "JIRA_TOKEN": "",
  "JIRA_PROJECT": ""
}
```

EC2의 `/opt/qa-platform/.env` 파일에는 실제 사용하는 값만 기록하고 권한을 제한한다.

```dotenv
ADMIN_SETUP_CODE=<Secrets Manager에서 받은 값>
LLM_PROVIDER=openai
OPENAI_API_KEY=<Secrets Manager에서 받은 값>
OPENAI_MODEL=gpt-4o-mini

MONITORING_ADDON_ENABLED=true
K6_HISTORY_ENABLED=true
MONITORING_ADDON_DB_ENABLED=true
PROMETHEUS_ADDON_ENABLED=true
GRAFANA_LINK_ENABLED=false
```

```bash
chmod 600 /opt/qa-platform/.env
```

권장 자동화는 부팅 또는 배포 시 인스턴스 역할로 Secrets Manager를 읽어 `.env`를 원자적으로 갱신하는 방식이다. Secret 값을 User Data, AMI, Git, CloudWatch 로그 또는 셸 히스토리에 남기지 않는다.

`GRAFANA_LINK_ENABLED=false`인 이유는 현재 화면이 접속 호스트의 `:3000`, `:9090`을 직접 참조하고 Grafana 익명 임베딩을 전제로 하기 때문이다. 공개 운영에서는 이 포트를 닫고 모니터링은 SSM 포트 포워딩 또는 별도 인증 프록시를 통해서만 본다.

## 10. EC2용 Compose 작성

`/opt/qa-platform/compose.yml`을 다음 형태로 작성한다. `<ACCOUNT_ID>`, 이미지 태그는 실제 값으로 바꾼다.

```yaml
services:
  qa-platform:
    image: <ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com/ai-agent-quality-platform:<GIT_SHA>
    env_file:
      - .env
    environment:
      PYTHONUNBUFFERED: "1"
      TZ: "Asia/Seoul"
    ports:
      - "8000:8000"
    volumes:
      - /opt/qa-platform/data:/app/data
      - /opt/qa-platform/reports:/app/reports
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 15s

  prometheus:
    image: prom/prometheus:<검증한-고정-버전>
    command:
      - --config.file=/etc/prometheus/prometheus.yml
    ports:
      - "127.0.0.1:9090:9090"
    volumes:
      - /opt/qa-platform/infra/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    restart: unless-stopped

  grafana:
    image: grafana/grafana:<검증한-고정-버전>
    ports:
      - "127.0.0.1:3000:3000"
    environment:
      GF_AUTH_ANONYMOUS_ENABLED: "false"
      GF_SECURITY_ADMIN_USER: "admin"
      GF_SECURITY_ADMIN_PASSWORD: "<별도 Secret으로 주입>"
    volumes:
      - grafana-data:/var/lib/grafana
      - /opt/qa-platform/infra/grafana/provisioning:/etc/grafana/provisioning:ro
      - /opt/qa-platform/infra/grafana/dashboards:/var/lib/grafana/dashboards:ro
    depends_on:
      - prometheus
    restart: unless-stopped

volumes:
  prometheus-data:
  grafana-data:
```

운영에서는 저장소의 `latest` 이미지 태그 대신 검증한 고정 버전을 사용한다. Prometheus 설정의 대상은 같은 Compose 네트워크의 `qa-platform:8000`이어야 한다.

ECR 로그인 후 기동한다.

```bash
aws ecr get-login-password --region ap-northeast-2 \
  | docker login --username AWS --password-stdin \
    <ACCOUNT_ID>.dkr.ecr.ap-northeast-2.amazonaws.com

cd /opt/qa-platform
docker compose pull
docker compose up -d
docker compose ps
curl --fail http://localhost:8000/health
```

## 11. ALB, HTTPS, 도메인 설정

### 11.1 Target Group

- Target type: Instances
- Protocol/Port: HTTP / 8000
- Health check path: `/health`
- Success codes: `200`
- Interval: 30초
- Healthy threshold: 2
- Unhealthy threshold: 3

EC2를 Target Group에 등록하고 상태가 `healthy`인지 확인한다.

### 11.2 ACM 인증서

ACM에서 `qa.example.com` 인증서를 요청하고 DNS 검증을 완료한다. 인증서는 ALB와 같은 리전에 생성한다.

### 11.3 ALB Listener

- 443/HTTPS: ACM 인증서 연결 → Target Group 전달
- 80/HTTP: `HTTPS:443`으로 301 리다이렉트

### 11.4 Route 53

Hosted Zone에서 `qa.example.com` A/AAAA Alias 레코드를 만들고 ALB를 대상으로 지정한다.

확인:

```bash
curl -I https://qa.example.com/health
curl -I http://qa.example.com/health
```

HTTP 요청은 HTTPS로 리다이렉트되고 HTTPS 헬스 체크는 200이어야 한다.

## 12. 최초 서비스 오픈

1. `https://qa.example.com/signup`에 접속한다.
2. `ADMIN_SETUP_CODE`를 아는 운영 담당자가 최초 관리자 계정을 생성한다.
3. 관리자 계정 생성 직후 `ADMIN_SETUP_CODE`를 새 값으로 교체하거나 가입 정책에 맞게 관리한다.
4. 일반 사용자의 가입, 승인, 로그인, 로그아웃을 확인한다.
5. 데이터셋 업로드, QA 실행, 결과 다운로드를 확인한다.
6. VOC 분석을 사용한다면 LLM API가 정상 호출되는지 확인한다.
7. Jira/Webhook을 사용한다면 테스트용 대상에서 알림을 검증한다.
8. `/monitoring-addon`과 `/metrics-addon`을 확인한다.

공개 오픈 전에는 다음 코드 보완이 필요하다.

- 세션 쿠키에 `Secure` 적용. 현재 코드는 `HttpOnly`, `SameSite=Lax`만 사용한다.
- 세션 서명/만료와 서버 측 영속 저장. 현재 세션은 메모리라 재시작 시 전부 로그아웃된다.
- 로그인 제한 저장소를 Redis 등으로 이전. 현재 IP 제한도 프로세스 메모리다.
- CSRF 방어와 보안 헤더(HSTS, CSP, X-Content-Type-Options) 점검
- ALB에 AWS WAF 적용
- Grafana 익명 접근 비활성화

위 조치 전에는 인터넷 전체 공개보다 회사 VPN, 사내망 또는 허용 IP 기반 접근을 권장한다.

## 13. 배포·업데이트·롤백

### 13.1 새 버전 배포

개발 PC 또는 CI에서:

```bash
pytest -q
export IMAGE_TAG=$(git rev-parse --short HEAD)
docker build --build-arg GIT_SHA="$IMAGE_TAG" -t "$ECR_REPOSITORY:$IMAGE_TAG" .
docker tag "$ECR_REPOSITORY:$IMAGE_TAG" \
  "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:$IMAGE_TAG"
docker push \
  "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY:$IMAGE_TAG"
```

EC2에서:

```bash
cd /opt/qa-platform
# compose.yml의 이미지 태그를 새 Git SHA로 변경
docker compose pull qa-platform
docker compose up -d qa-platform
docker compose ps
curl --fail http://localhost:8000/health
```

배포 직후 다음을 확인한다.

- ALB Target 상태 `healthy`
- `/health` 응답의 `git_sha`가 새 버전과 일치
- 로그인, 업로드, 핵심 분석 1건
- 애플리케이션 오류 로그

현재 로그인 세션이 메모리이므로 컨테이너 교체 시 사용자가 다시 로그인해야 한다.

### 13.2 롤백

1. `compose.yml`의 이미지 태그를 직전 정상 Git SHA로 되돌린다.
2. `docker compose pull qa-platform`
3. `docker compose up -d qa-platform`
4. `/health`, `git_sha`, 핵심 기능을 확인한다.

DB 스키마 또는 데이터 형식이 바뀐 릴리스는 이미지 롤백 전에 백업 호환성을 확인한다.

## 14. 백업과 복구

### 14.1 백업 대상

필수:

- `/opt/qa-platform/data/`
- `/opt/qa-platform/reports/`
- 운영 Compose 및 Prometheus/Grafana 설정
- Secrets Manager Secret 자체와 복구 권한

제외 가능:

- 컨테이너 레이어와 재생성 가능한 이미지
- 임시 캐시

### 14.2 권장 정책

- EBS 암호화 활성화
- Data Lifecycle Manager 또는 AWS Backup으로 매일 스냅샷
- 일일 7개, 주간 4개, 월간 3개 등 조직 정책에 맞춘 보존
- 월 1회 별도 복구 인스턴스에서 실제 복구 훈련
- 중요도에 따라 교차 리전 또는 교차 계정 복사

SQLite 일관성을 높이려면 애플리케이션을 잠시 중지한 뒤 백업한다.

```bash
cd /opt/qa-platform
docker compose stop qa-platform
# 이 시점에 EBS 스냅샷 또는 파일 백업 수행
docker compose start qa-platform
curl --fail http://localhost:8000/health
```

### 14.3 복구 절차

1. 장애 시각 이전의 정상 EBS 스냅샷을 선택한다.
2. 동일 AZ에 새 EBS 볼륨을 생성한다.
3. 새 또는 기존 EC2에 연결해 임시 경로에 읽기 전용으로 마운트한다.
4. `data/`의 SQLite 파일과 `reports/` 내용을 검사한다.
5. 앱을 중지하고 복구 대상 디렉터리로 교체한다.
6. 파일 소유자와 권한을 복원한다.
7. 앱을 시작하고 `/health`, 로그인, 게시판, 실행 이력을 검증한다.

원본 볼륨을 즉시 삭제하지 않는다. 복구 검증 완료 후 보존 정책에 따라 처리한다.

## 15. 모니터링과 경보

최소 CloudWatch Alarm:

- ALB `UnHealthyHostCount >= 1`
- ALB `HTTPCode_Target_5XX_Count`
- ALB TargetResponseTime p95
- EC2 CPUUtilization
- EBS BurstBalance, VolumeQueueLength, 여유 공간
- EC2 StatusCheckFailed

컨테이너 로그는 CloudWatch Agent 또는 Docker 로그 드라이버로 CloudWatch Logs에 전송한다. API 키, 토큰, 개인정보가 로그에 포함되지 않도록 필터링하고 보존 기간을 지정한다.

로컬 확인:

```bash
cd /opt/qa-platform
docker compose ps
docker compose logs --tail=200 qa-platform
curl --fail http://localhost:8000/health
df -h
free -m
```

Grafana가 로컬호스트에만 바인딩되어 있으면 SSM 포트 포워딩으로 접근한다. 운영 환경에서는 임시 터널만 열고, Grafana 기본 관리자 암호를 사용하지 않는다.

## 16. 장애 대응

### ALB 502/503

1. Target Group 상태 확인
2. `docker compose ps`
3. `docker compose logs --tail=200 qa-platform`
4. EC2 내부에서 `curl http://localhost:8000/health`
5. 보안 그룹의 ALB → EC2:8000 규칙 확인
6. EBS 용량과 inode 확인

### 컨테이너 반복 재시작

1. `.env` 형식과 필수 값 확인
2. `data/`, `reports/` 쓰기 권한 확인
3. SQLite `-wal`, `-shm` 파일과 디스크 상태 확인
4. OOM 여부와 메모리 확인
5. 직전 정상 이미지로 롤백

### 로그인 전체 해제

컨테이너 재시작 시 현재 설계상 정상적으로 발생한다. 영속 세션 도입 전까지 사용자에게 재로그인을 안내한다.

### 데이터 손상 의심

1. 쓰기 작업 중단
2. 앱 컨테이너 중지
3. 현재 EBS 스냅샷 보존
4. 정상 시점 스냅샷으로 별도 볼륨 복구
5. 원본을 덮어쓰기 전에 복구본 검증

## 17. 고가용성·공개 서비스 전환 조건

다음 변경을 완료한 후 ECS/Fargate 또는 EC2 Auto Scaling으로 전환한다.

| 현재 | 전환 대상 |
|---|---|
| SQLite 3개 | Amazon RDS for PostgreSQL Multi-AZ |
| 메모리 로그인 세션 | ElastiCache for Redis 또는 서명된 영속 세션 |
| `RUN_REGISTRY` 메모리 상태 | SQS + Worker + DB 상태 저장 |
| 로컬 `reports/` | S3, 필요 시 CloudFront |
| 로컬 업로드 파일 | S3 Presigned URL |
| 단일 앱 컨테이너 | ECS Service 2 Task 이상, 2개 AZ |
| 로컬 Prometheus | Amazon Managed Service for Prometheus |
| 로컬 Grafana | Amazon Managed Grafana 또는 인증된 별도 서비스 |
| 수동 배포 | CodePipeline/CodeBuild 또는 GitHub Actions + ECR/ECS |

전환 후에야 Rolling/Blue-Green 배포, Auto Scaling, 무중단 장애 조치가 의미 있게 동작한다.

## 18. 최종 오픈 체크리스트

- [ ] 전체 테스트 통과
- [ ] ECR 이미지 취약점 스캔 결과 검토
- [ ] `.env`와 Secret이 이미지/Git/로그에 없음
- [ ] EBS 암호화와 자동 스냅샷 설정
- [ ] ALB 외에 8000/3000/9090/22가 외부에 노출되지 않음
- [ ] HTTPS와 HTTP→HTTPS 리다이렉트 정상
- [ ] 관리자 계정과 가입 정책 확인
- [ ] Grafana 익명 접근 비활성화
- [ ] CloudWatch 로그와 핵심 Alarm 동작
- [ ] 롤백 절차 리허설
- [ ] 백업 복구 리허설
- [ ] 개인정보·보존기간·삭제 정책 확정
- [ ] 인터넷 공개 시 Secure Cookie, 영속 세션, CSRF, 보안 헤더, WAF 적용

## 19. 참고 문서

- Amazon ECR 이미지 Push: https://docs.aws.amazon.com/AmazonECR/latest/userguide/docker-push-ecr-image.html
- Amazon Linux 2023에서 컨테이너 이미지 준비: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/create-container-image.html
- ALB HTTPS Listener: https://docs.aws.amazon.com/elasticloadbalancing/latest/application/create-https-listener.html
- ACM DNS 검증: https://docs.aws.amazon.com/acm/latest/userguide/dns-validation.html
- Route 53에서 ALB로 라우팅: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-to-elb-load-balancer.html
- EBS 스냅샷: https://docs.aws.amazon.com/ebs/latest/userguide/ebs-creating-snapshot.html
- Data Lifecycle Manager: https://docs.aws.amazon.com/ebs/latest/userguide/dlm-elements.html
- Systems Manager Session Manager: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html


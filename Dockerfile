FROM python:3.12-slim

# k6 설치 - 모니터링 애드온의 "웹에서 직접 k6 실행" 기능이 컨테이너 안에서도 동작하려면
# k6 실행 파일이 PATH에 있어야 함 (없어도 앱 자체는 정상 기동하고, 이 기능만 안내 메시지 표시).
# apt 저장소 대신 공식 정적 바이너리를 직접 받음 - keyserver/dirmngr에 의존하지 않아 더 안정적.
ARG K6_VERSION=v0.54.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/grafana/k6/releases/download/${K6_VERSION}/k6-${K6_VERSION}-linux-amd64.tar.gz" \
       | tar xz --strip-components 1 -C /usr/local/bin "k6-${K6_VERSION}-linux-amd64/k6"

# 컨테이너 기본 타임존이 UTC라 datetime.now()로 찍히는 게시판/VOC분석/실행이력 시각이
# 실제 한국시간(KST)보다 9시간 느리게 기록되던 문제 수정 - 이미지 자체를 KST로 고정.
ENV TZ=Asia/Seoul
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/reports/exports /app/data

# .dockerignore가 .git을 빌드 컨텍스트에서 제외하므로(이미지 크기/보안), 컨테이너 안에서
# `git rev-parse`를 실행해도 항상 실패한다 - 호스트에서 빌드 시점에 커밋을 계산해 빌드
# 인자로 주입하고, app/main.py::_detect_git_sha()가 이 환경변수를 최우선으로 읽는다.
ARG GIT_SHA=unknown
ENV GIT_SHA=${GIT_SHA}
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status == 200 else 1)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

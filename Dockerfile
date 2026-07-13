FROM python:3.12-slim

# k6 설치 - 모니터링 애드온의 "웹에서 직접 k6 실행" 기능이 컨테이너 안에서도 동작하려면
# k6 실행 파일이 PATH에 있어야 함 (없어도 앱 자체는 정상 기동하고, 이 기능만 안내 메시지 표시).
# apt 저장소 대신 공식 정적 바이너리를 직접 받음 - keyserver/dirmngr에 의존하지 않아 더 안정적.
ARG K6_VERSION=v0.54.0
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/grafana/k6/releases/download/${K6_VERSION}/k6-${K6_VERSION}-linux-amd64.tar.gz" \
       | tar xz --strip-components 1 -C /usr/local/bin "k6-${K6_VERSION}-linux-amd64/k6"

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/reports/exports /app/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status == 200 else 1)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

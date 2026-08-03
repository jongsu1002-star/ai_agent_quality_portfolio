# Docker 포트폴리오 영상 촬영 체크리스트

## 실행 환경

- [x] `docker compose up -d --build` 이미지 빌드
- [x] `qa-platform` health 정상
- [x] `prometheus` health 정상
- [x] `grafana` health 정상
- [x] `/health` HTTP 200
- [x] `/metrics-addon` HTTP 200
- [x] `/monitoring-addon?demo=1` HTTP 200
- [x] 데모 스크립트 15단계 및 실제 경로 보존 확인
- [x] VOC Improved 합성 실행 코드와 `LLM Judge: SKIPPED` 표시 확인

## 촬영 전

- [ ] Chrome을 1920×1080 전체 화면으로 배치
- [ ] OBS 화면 캡처 소스와 출력 폴더 확인
- [ ] API 키·Jira·웹훅·이메일·IP 마스킹 확인
- [ ] 관리자 메뉴가 표시되는 승인된 로컬 세션 확인
- [ ] 합성 QA 데이터셋과 테스트 케이스 업로드 준비
- [ ] 합성 VOC 파일 업로드 준비

## 촬영 동작

- [ ] 설정 → 실행 → 대시보드 → 모니터링 순회
- [ ] QA 파일 2개 실제 업로드와 파이프라인 실행
- [ ] 모니터링 애드온 정상 이동
- [ ] k6 `/health`, 1 VU, 10초 실제 실행
- [ ] Prometheus·Grafana 임베드 확인
- [ ] 게시판 목록·검색·작성 UI 확인(저장 안 함)
- [ ] VOC 합성 파일 업로드
- [ ] VOC Improved 5단계 합성 테스트 실행
- [ ] VOC 결과·이력·품질 대시보드와 `SKIPPED` 확인
- [ ] 사용자 관리·오류 로그·접근 허용 IP 확인(변경 안 함)

## 촬영 후 증적

- 최종 파일: `demo/video/portfolio-demo-docker.mp4`
- OBS 원본: 촬영 후 기록
- 재생 시간: 촬영 후 기록
- 해상도: 촬영 후 기록
- 파일 크기: 촬영 후 기록
- 회귀 테스트: 촬영 전 최종 실행 결과 기록
- 외부 API 호출: 없음


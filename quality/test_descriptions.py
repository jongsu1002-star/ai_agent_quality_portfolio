"""각 pytest 테스트 함수가 "무엇을 확인하는지" 한글 한 줄 설명 모음.

테스트 함수 자체에는 docstring이 없어서(관례상 이름만으로 의도를 표현), 여기 별도
테이블로 이름 -> 설명을 관리합니다. `테스트_결과.md`의 "파일별 결과" 표에서
`test_report.py`가 이 딕셔너리를 조회해 "설명" 칼럼을 채웁니다.

키는 pytest 노드ID에서 파라미터 대괄호(`[...]`)를 뗀 "기본 함수명"입니다
(`@pytest.mark.parametrize`로 여러 번 도는 테스트도 설명은 하나만 적어두면 됨).
새 테스트를 추가했는데 여기 등록을 안 하면 "설명" 칸에는 그냥 "-"이 표시됩니다 -
문서가 깨지진 않으니, 시간 날 때 채워 넣으면 됩니다.
"""

from __future__ import annotations

TEST_DESCRIPTIONS: dict[str, str] = {
    # tests/test_docs_reference_integrity.py
    "test_static_doc_exists": "필수 문서 파일(매뉴얼/설계서/프로세스 명세서/접속가이드)이 실제로 존재하는지 확인",
    "test_no_unescaped_pipe_inside_code_span_on_table_rows": "문서의 표 안 코드span에 이스케이프 안 된 파이프(\\|)가 있어 표가 깨지지 않는지 확인",
    "test_design_spec_reference_still_exists": "설계서.md가 인용하는 클래스/함수명이 실제 소스에 그대로 남아있는지 확인",
    "test_process_spec_reference_still_exists": "프로세스_명세서.md가 인용하는 심볼이 실제 소스에 그대로 남아있는지 확인",
    "test_user_manual_reference_still_exists": "사용자_매뉴얼.md가 인용하는 API 경로/심볼이 실제로 존재하는지 확인",
    "test_network_guide_reference_still_exists": "팀원용_접속가이드.md가 인용하는 심볼/설정이 실제로 존재하는지 확인",
    # tests/test_docs_generation.py
    "test_write_defect_report_doc_lists_failing_cases": "결함보고서.md가 실패 케이스와 회귀/기능시험 결과를 올바르게 담아내는지 확인",
    "test_render_test_results_markdown_summarizes_pass_fail": "테스트 결과 마크다운이 총계/통과/실패 건수를 올바르게 요약하는지 확인",
    "test_write_test_results_doc_creates_file": "테스트_결과.md 파일이 실제로 생성되고 전체 통과 문구가 들어가는지 확인",
    # tests/test_api_features.py
    "test_dataset_upload_json_endpoint": "JSON 데이터셋 업로드 API가 케이스 수를 올바르게 반환하는지 확인",
    "test_dataset_reset_clears_active_dataset": "데이터셋 초기화 시 활성 데이터셋이 비워지고 기본값으로 돌아가는지 확인",
    "test_dataset_history_lists_uploads_with_active_flag": "데이터셋 업로드 이력 목록이 최신순 정렬과 활성 표시를 올바르게 보여주는지 확인",
    "test_dataset_select_reactivates_a_previous_upload": "이력에서 이전 데이터셋을 선택하면 그 데이터셋이 다시 활성화되는지 확인",
    "test_dataset_select_rejects_path_outside_dataset_dir": "데이터셋 디렉터리 밖 경로를 선택하려 하면 거부되는지 확인(경로 조작 방지)",
    "test_excel_template_round_trip_runs_against_uploaded_cases": "엑셀 양식을 내려받아 그대로 업로드한 뒤 파이프라인을 실행해도 정상 동작하는지 확인",
    "test_run_status_and_history_endpoints": "실행 상태 조회/이력 조회 API가 정상 동작하는지 확인",
    # tests/test_env_and_ui.py
    "test_env_settings_are_loaded_and_downloads_are_named": ".env 값이 설정 API에 반영되고 CSV 다운로드 파일명이 올바른지 확인",
    # tests/test_evaluators_and_notifiers.py
    "test_retrieval_evaluator_computes_real_recall_precision_mrr": "검색품질 평가자가 Recall/Precision/MRR을 실제 값으로 정확히 계산하는지 확인",
    "test_apply_pass_policy_falls_back_to_rule_when_llm_unavailable": "LLM 판정이 없을 때 pass_policy와 무관하게 룰 판정으로 대체되는지 확인",
    "test_compute_agreement": "룰 판정과 LLM 판정의 일치/불일치/평가없음 상태를 올바르게 계산하는지 확인",
    "test_load_config_applies_overrides": "설정 오버라이드가 커넥터/임계값에 올바르게 반영되는지 확인",
    "test_jira_notifier_uses_base64_basic_auth": "Jira 알림이 Basic 인증 헤더를 base64로 올바르게 인코딩하는지 확인",
    "test_jira_notifier_creates_ticket_for_high_failure_category": "실패율이 높은 카테고리에 대해 Jira 티켓이 생성되는지 확인",
    "test_discord_notifier_uses_discord_schema": "Discord 알림이 Slack과 다른 Discord 전용 스키마(content/embeds)로 전송되는지 확인",
    # tests/test_export_and_config_persistence.py
    "test_settings_persistence_and_export_routes": "설정 저장 후 조회 및 리포트 내보내기 라우트가 정상 동작하는지 확인",
    # tests/test_integrations.py
    "test_excel_loader_handles_json_dataset": "excel_io 로더가 JSON 데이터셋 파일도 문제없이 읽어오는지 확인",
    "test_jira_notifier_handles_disabled_config": "Jira 연동이 꺼져 있을 때 알림을 조용히 건너뛰는지 확인",
    "test_slack_notifier_handles_missing_webhook": "Slack 웹훅이 설정되지 않았을 때 skipped 상태를 반환하는지 확인",
    # tests/test_new_endpoints_and_modules.py
    "test_health_endpoint_uses_health_checker": "/health 엔드포인트가 HealthChecker 결과를 그대로 반환하는지 확인",
    "test_connector_defaults_endpoint": "커넥터 기본값 API가 dataset_only 모드를 기본으로 반환하는지 확인",
    "test_monitoring_summary_endpoint_tracks_requests": "모니터링 요약 API가 요청수/응답시간/헬스 상태를 실제로 집계해 반환하는지 확인",
    "test_monitoring_target_rejects_url_without_http_scheme": "외부 모니터링 대상 등록 시 http(s)로 시작하지 않는 URL은 거부되는지 확인",
    "test_monitoring_target_add_list_and_remove_round_trip": "외부 모니터링 대상 등록/목록조회/삭제 API가 정상 동작하고 최소 체크주기가 강제되는지 확인",
    # tests/test_external_monitor.py
    "test_probe_once_records_success_for_2xx_response": "probe_once가 2xx 응답을 성공으로 기록하는지 확인",
    "test_probe_once_records_failure_on_request_exception": "probe_once가 네트워크 예외를 실패 결과로 안전하게 변환하는지 확인",
    "test_registry_summary_computes_uptime_and_avg_response_time": "외부 대상 레지스트리가 최근 체크 이력으로 가동률/평균응답시간을 올바르게 계산하는지 확인",
    "test_registry_persists_targets_across_reload": "외부 모니터링 대상 목록이 파일에 저장되어 재시작(재로딩) 후에도 유지되는지 확인",
    "test_registry_add_enforces_minimum_interval": "너무 짧은 체크 주기를 입력해도 최소 주기(10초)로 강제 상향되는지 확인",
    "test_monitoring_summary_response_shape_is_unchanged_by_monitoring_addon": "모니터링 애드온이 얹혀도 기존 /api/monitoring/summary 응답의 key 구조가 그대로인지 확인(회귀 방지)",
    # tests/test_monitoring_addon_db.py
    "test_schema_creation_is_idempotent_across_instances": "같은 DB 파일에 대해 여러 번 스키마를 생성해도 에러 없이 멱등한지 확인",
    "test_insert_snapshot_and_recent_snapshots_orders_newest_first": "스냅샷 저장 후 조회 시 최신순으로 정렬되는지 확인",
    "test_insert_k6_run_is_idempotent_by_run_id": "같은 run_id의 k6 실행을 두 번 저장해도 중복 저장(및 threshold 중복)이 안 되는지 확인",
    "test_list_k6_runs_supports_pagination_and_result_filter": "k6 실행 이력 조회가 페이지네이션과 Pass/Fail 필터를 올바르게 지원하는지 확인",
    "test_get_latest_k6_run_returns_most_recently_inserted": "최신 k6 실행 조회가 가장 최근에 저장된 실행을 반환하는지 확인",
    "test_get_k6_run_returns_none_for_unknown_run_id": "존재하지 않는 run_id 조회 시 None을 반환하는지 확인",
    # tests/test_monitoring_addon_k6_import.py
    "test_import_missing_file_returns_no_data": "k6 결과 파일이 없을 때 no_data 상태를 반환하는지 확인",
    "test_import_invalid_json_returns_invalid_json_status": "k6 결과 파일이 깨진 JSON일 때 invalid_json 상태를 반환하는지 확인",
    "test_import_missing_required_fields_returns_invalid_json_status": "k6 결과 JSON에 필수 필드가 없을 때도 invalid_json으로 처리되는지 확인",
    "test_import_valid_result_stores_it_and_is_idempotent": "정상 k6 결과를 저장하고, 재수입 시 중복 저장하지 않는지 확인",
    # tests/test_monitoring_addon_api.py
    "test_k6_latest_returns_no_data_when_nothing_imported": "k6 최신 결과 API가 데이터 없을 때 no_data를 반환하는지 확인",
    "test_k6_runs_list_is_empty_initially": "k6 실행 이력 API가 데이터 없을 때 빈 목록을 반환하는지 확인",
    "test_k6_run_detail_404_for_unknown_run": "존재하지 않는 실행 상세 조회 시 404를 반환하는지 확인",
    "test_k6_import_endpoint_and_latest_round_trip": "k6 결과 수동 import API 이후 최신/이력/상세 API가 일관되게 반영하는지 확인",
    "test_history_summary_reflects_inserted_snapshots": "장기 스냅샷 조회 API가 저장된 스냅샷을 올바르게 반환하는지 확인",
    "test_metrics_addon_returns_prometheus_text_with_self_metrics": "/metrics-addon이 자체 운영 지표를 Prometheus 텍스트 포맷으로 반환하는지 확인",
    "test_metrics_addon_includes_k6_metrics_after_import": "/metrics-addon이 k6 import 이후 k6 관련 지표도 포함하는지 확인",
    "test_monitoring_addon_page_is_served_separately_from_main_dashboard": "/monitoring-addon 페이지가 기존 메인 대시보드와 별도로 정상 서빙되는지 확인",
    "test_docs_endpoint_returns_current_file_content": "문서 조회 API가 모든 문서 종류에 대해 파일명/본문을 올바르게 반환하는지 확인",
    "test_docs_endpoint_rejects_unknown_key": "존재하지 않는 문서 키를 요청하면 404를 반환하는지 확인",
    "test_get_local_ip_returns_a_dotted_quad_or_none": "get_local_ip()가 유효한 IPv4 형식 또는 None을 반환하는지 확인",
    "test_run_rejects_empty_techniques": "테스트 기법을 하나도 선택하지 않으면 실행 요청이 거부되는지 확인",
    "test_run_rejects_unknown_technique": "존재하지 않는 기법명을 요청하면 거부되는지 확인",
    "test_run_result_and_run_detail_endpoints": "실행 결과 조회 및 실행 상세 조회 API가 올바른 run_id를 반환하는지 확인",
    "test_run_detail_rejects_path_traversal": "실행 상세 조회 API가 경로 조작(path traversal) 시도를 거부하는지 확인",
    "test_jira_tickets_endpoint_returns_list": "Jira 티켓 목록 API가 배열 형태로 응답하는지 확인",
    "test_quality_gate_reports_fail_below_threshold": "통과율이 임계값보다 낮으면 품질 게이트가 fail을 반환하는지 확인",
    "test_run_with_llm_provider_none_skips_llm_even_with_a_key_available": "LLM 제공자를 '사용 안 함'으로 설정하면 API 키가 있어도 LLM 평가를 건너뛰는지 확인",
    "test_run_with_custom_llm_provider_uses_configured_header_and_endpoint": "커스텀 LLM 제공자가 사용자 지정 헤더/엔드포인트로 정확히 호출되는지 확인",
    "test_run_with_anthropic_llm_provider_calls_messages_api": "Anthropic 제공자가 Messages API를 올바른 헤더로 호출하는지 확인",
    "test_benchmark_pipeline_times_a_real_run": "파이프라인 벤치마크가 실제 실행 시간을 측정해 반환하는지 확인",
    # tests/test_llm_client_providers.py
    "test_openai_provider_is_default_and_reads_env": "OpenAI가 기본 LLM 제공자이며 환경변수에서 키를 읽어오는지 확인",
    "test_none_provider_is_always_disabled_even_with_a_key": "제공자를 '사용 안 함'으로 지정하면 키가 있어도 항상 비활성 상태인지 확인",
    "test_custom_provider_requires_both_key_and_endpoint": "커스텀 제공자는 키와 엔드포인트가 모두 있어야 활성화되는지 확인",
    "test_custom_provider_sends_configured_header_name_not_authorization": "커스텀 제공자가 Authorization이 아닌 사용자 지정 헤더명으로 키를 전송하는지 확인",
    "test_anthropic_provider_reads_env_and_defaults": "Anthropic 제공자가 환경변수와 기본 모델/엔드포인트를 올바르게 읽어오는지 확인",
    "test_anthropic_provider_calls_messages_api_and_parses_text_block": "Anthropic 제공자가 Messages API를 호출하고 응답 텍스트 블록을 올바르게 파싱하는지 확인",
    "test_anthropic_provider_unwraps_markdown_json_fences": "Anthropic 응답이 마크다운 코드펜스로 감싸져 와도 JSON을 올바르게 벗겨내는지 확인",
    # tests/test_pipeline.py
    "test_pipeline_generates_report_for_simple_case": "단순 케이스 1건에 대해 파이프라인이 리포트를 정상적으로 생성하는지 확인",
    "test_pipeline_fails_case_with_missing_retrieval_docs": "검색된 문서가 없을 때 검색품질 평가가 실패로 처리되는지 확인",
    "test_pipeline_dataset_only_missing_existing_answer_errors": "dataset_only 모드에서 저장된 답변이 없으면 오류로 처리되는지 확인",
    "test_pipeline_functional_technique_runs_connector_contract_probes": "functional 기법이 커넥터 계약 검증용 합성 질문 4건을 실행하는지 확인",
    # tests/test_reporting_and_ui.py
    "test_report_writer_creates_csv_and_markdown": "reporter가 JSON/CSV/마크다운 리포트 파일을 모두 생성하는지 확인",
    "test_app_serves_dashboard_page": "루트 경로가 대시보드 HTML 페이지를 정상적으로 서빙하는지 확인",
    # tests/test_settings_and_exports.py
    "test_load_settings_and_export_downloads": "설정 저장 후 CSV/MD/JSON 형식의 리포트 다운로드가 모두 동작하는지 확인",
    # tests/test_ui_and_env_docs.py
    "test_settings_endpoint_returns_defaults_and_docs_exist": "설정 API가 기본값을 반환하는지 확인",
    # tests/test_ui_config_and_exports.py
    "test_template_download_and_report_routes": "엑셀 양식 다운로드와 실행/리포트 라우트가 정상 동작하는지 확인",
}

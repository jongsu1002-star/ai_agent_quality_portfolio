"""VOC 품질평가 보고서/결함보고서의 실제 git 개정 이력을 docs/report_versions.json으로 추출.

Docker 이미지는 .dockerignore가 .git을 빌드 컨텍스트에서 제외하므로(빌드 안정성/이미지
경량화를 위한 의도적 결정), 컨테이너 런타임에는 git 명령을 쓸 수 없다 - 그래서 "버전
히스토리" 기능은 런타임에 git을 조회하는 대신, 호스트에서(이 스크립트로) 미리 추출한
스냅샷 JSON을 일반 파일로 커밋해두고 서버는 그 파일만 읽는다.

사용법(수동): 두 문서 중 하나를 의미 있게 개정한 뒤 이 스크립트를 실행해
docs/report_versions.json을 최신 상태로 갱신하고, 갱신된 JSON도 함께 커밋한다.

자동화: `git config core.hooksPath githooks` 1회 설정 후에는 `githooks/post-commit`이
이 스크립트를 `--auto-commit-if-changed`로 호출해 필요할 때만 알아서 재실행하고 결과를
별도 커밋으로 붙인다(githooks/README.md 참고). git show는 이미 커밋된 내용만 읽을 수
있어 pre-commit 시점엔 "방금 만든 커밋"이 아직 log에 없으므로, 반드시 그 커밋이 끝난
뒤(post-commit)에 실행해야 한다 - 그래서 훅과 실제 문서 커밋이 항상 별도 커밋 2개로
나뉜다(같은 커밋에 합칠 수 없음).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "docs" / "report_versions.json"
OUTPUT_RELATIVE = "docs/report_versions.json"

TRACKED_DOCS = {
    "voc_quality_report": "docs/VOC_분석_파이프라인_품질평가_보고서.md",
    "voc_defect_report": "docs/VOC_분석_파이프라인_결함보고서.md",
}


def _run(args: list[str]) -> str:
    result = subprocess.run(args, cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"git 명령 실패: {' '.join(args)}\n{result.stderr}")
    return result.stdout


def _extract_versions(relative_path: str) -> list[dict]:
    log_output = _run(["git", "log", "--follow", "--format=%h|%ai|%s", "--", relative_path])
    versions = []
    for line in log_output.strip().splitlines():
        commit, date, message = line.split("|", 2)
        content = _run(["git", "show", f"{commit}:{relative_path}"])
        versions.append({"commit": commit, "date": date, "message": message, "content": content})
    return versions


def _regenerate() -> dict[str, list[dict]]:
    snapshot = {key: _extract_versions(path) for key, path in TRACKED_DOCS.items()}
    OUTPUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def _head_touched_tracked_docs() -> bool:
    # -z: git이 기본으로 켜 두는 core.quotePath 때문에 비-ASCII(한글) 경로가
    # "docs/\355..." 식 8진수 이스케이프 문자열로 나와 TRACKED_DOCS와 매칭이 안 되는
    # 문제가 있어, NUL 구분 원문 출력을 강제해 그 quoting을 우회한다.
    output = _run(["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "HEAD"])
    changed = set(filter(None, output.split("\0")))
    return not changed.isdisjoint(TRACKED_DOCS.values())


def _auto_commit_if_changed() -> int:
    """post-commit 훅 전용: HEAD가 추적 대상 문서를 건드렸을 때만 재생성하고,
    실제로 내용이 바뀌었을 때만 별도 커밋을 만든다(불필요한 빈 커밋 방지)."""
    if not _head_touched_tracked_docs():
        return 0

    _regenerate()

    status = _run(["git", "status", "--porcelain", "--", OUTPUT_RELATIVE])
    if not status.strip():
        print("[snapshot] report_versions.json 변경 없음 - 커밋 생략")
        return 0

    _run(["git", "add", OUTPUT_RELATIVE])
    _run(["git", "commit", "-m", "docs: report_versions.json 자동 갱신 (post-commit hook)", "--quiet"])
    print("[snapshot] report_versions.json 갱신 후 자동 커밋 완료")
    return 0


def main() -> int:
    if "--auto-commit-if-changed" in sys.argv[1:]:
        return _auto_commit_if_changed()

    snapshot = _regenerate()
    for key, versions in snapshot.items():
        print(f"[snapshot] {key}: {len(versions)}개 버전 추출 -> {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

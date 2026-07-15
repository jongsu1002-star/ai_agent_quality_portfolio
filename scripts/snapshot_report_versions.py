"""VOC 품질평가 보고서/결함보고서의 실제 git 개정 이력을 docs/report_versions.json으로 추출.

Docker 이미지는 .dockerignore가 .git을 빌드 컨텍스트에서 제외하므로(빌드 안정성/이미지
경량화를 위한 의도적 결정), 컨테이너 런타임에는 git 명령을 쓸 수 없다 - 그래서 "버전
히스토리" 기능은 런타임에 git을 조회하는 대신, 호스트에서(이 스크립트로) 미리 추출한
스냅샷 JSON을 일반 파일로 커밋해두고 서버는 그 파일만 읽는다.

사용법: 두 문서 중 하나를 의미 있게 개정한 뒤 커밋 전에 이 스크립트를 다시 실행해
docs/report_versions.json을 최신 상태로 갱신하고, 갱신된 JSON도 함께 커밋한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "docs" / "report_versions.json"

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


def main() -> int:
    snapshot = {key: _extract_versions(path) for key, path in TRACKED_DOCS.items()}
    OUTPUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    for key, versions in snapshot.items():
        print(f"[snapshot] {key}: {len(versions)}개 버전 추출 -> {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

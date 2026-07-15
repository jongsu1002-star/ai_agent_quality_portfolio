# githooks/

`.git/hooks/`는 커밋되지 않아 클론마다 따로 설정해야 하므로, 저장소에 커밋할 수 있는
훅 디렉터리를 별도로 둔다. 클론 후 1회만 아래 명령으로 훅 경로를 이 디렉터리로 지정하면
이후 모든 커밋에 자동 적용된다.

```
git config core.hooksPath githooks
```

## post-commit

`docs/VOC_분석_파이프라인_품질평가_보고서.md` 또는
`docs/VOC_분석_파이프라인_결함보고서.md`를 건드린 커밋 뒤에
`scripts/snapshot_report_versions.py --auto-commit-if-changed`를 실행해
`docs/report_versions.json`을 재생성하고, 내용이 바뀌었으면 그 파일만 별도 커밋으로
자동 커밋한다. 두 문서와 무관한 커밋에는 아무 동작도 하지 않는다.

문서 커밋과 스냅샷 커밋이 항상 2개로 나뉘는 이유: 스냅샷 스크립트는 `git show`로
이미 커밋된 내용만 읽을 수 있어, 방금 만든 커밋을 반영하려면 그 커밋이 끝난 뒤에만
실행할 수 있다(pre-commit 시점엔 아직 log에 없음).

`core.hooksPath`를 설정하지 않았거나 python을 찾지 못하면 조용히 건너뛰므로,
그 경우 지금까지처럼 `python scripts/snapshot_report_versions.py`를 수동으로 실행해
`docs/report_versions.json`을 함께 커밋해야 한다.

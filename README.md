# docs-for-biz-support

정부·스타트업 지원사업을 매일 수집하고, 이전 정상 스냅샷과 비교해 HTML
메일과 GitHub Pages 아카이브를 발행하는 Python 서비스입니다.

## 운영 흐름

1. `daily.yml`의 `scrape` 작업이 각 소스를 수집합니다.
2. 소스별 성공·캐시 폴백 상태를 검증하고 Turso에 완성 스냅샷을 기록합니다.
3. `mail` 작업은 같은 KST 날짜의 완성 스냅샷만 읽습니다.
4. 5개 아카이브 저장소에 날짜 HTML·`archive.json`·`index.html`을 푸시합니다.
5. 날짜별 GitHub Pages URL의 게시 내용을 확인한 뒤 Gmail SMTP로 발송합니다.
6. SMTP `Message-ID`를 Turso와 `state/sent-YYYY-MM-DD.marker`에 기록합니다.

## 아카이브와 소스

| 아카이브 | 활성 소스 |
|---|---|
| `gov-support` | 기업마당, NIPA, IRIS, NTIS |
| `busan-startup` | 부산창업포털 실시간 접수중 API, 장기 운영시설 카탈로그 |
| `kstartup-biz` | K-Startup 사업화 |
| `kstartup-mentoring` | K-Startup R&D, 멘토링·컨설팅 |
| `kstartup-global` | K-Startup 시설·공간, 글로벌 |

부분 페이지나 전체 수집이 실패하면 해당 소스만 마지막 `complete` 스냅샷으로
대체합니다. 캐시도 없는 소스 실패는 전체 아카이브 실패로 처리해 대량의 거짓
종료·신규 전환을 방지합니다.

## 정부지원 메일 우선순위

`신규`와 우선조건을 동시에 만족한 공고는 활성 주기의 최초 확인일
(`active_since`)부터 7일간, 즉 D+0~D+6에 최상단
`신규 + 우선조건 동시충족` 영역에 유지됩니다. D+7부터는 일반
`우선조건 충족 — 부산·경남·경북 · 제조·AI · 중견·중소` 영역으로 이동합니다.
메일은 일부 대형 섹션을 제한하지만 GitHub Pages 날짜 아카이브에는 전체 건을
렌더링합니다.

## 로컬 실행

```bash
uv sync --frozen --extra dev
uv run --frozen python -m src.cli --scrape --dry-run --only gov-support
uv run --frozen pytest -q
uv run --frozen ruff check src tests
```

운영 환경 변수는 `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, `GMAIL_USER`,
`GMAIL_APP_PASSWORD`, `MAIL_TO`, `MAIL_CC_GOV_SUPPORT`,
`ARCHIVE_PUSH_TOKEN`입니다. 실제 비밀값은 GitHub Actions secrets에만 둡니다.

수동 복구는 `scrape.yml`과 `mail.yml`의 `workflow_dispatch`로 실행하며, 정기
스케줄은 중복 방지를 위해 `daily.yml`에만 있습니다.

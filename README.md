# docs-for-biz-support

정부·스타트업 지원사업을 매일 수집하고, 이전 정상 스냅샷과 비교해 HTML
메일과 GitHub Pages 아카이브를 발행하는 Python 서비스입니다.

## 운영 흐름

1. `daily.yml`이 Ubuntu에서 기업마당 공식 API 응답을 먼저 검증합니다.
2. Azure 연결이 실패하면 macOS 러너가 다른 네트워크에서 API 수집을 대신합니다.
3. 키가 포함되지 않은 검증 완료 응답을 `scrape` 작업에 전달해 각 소스를 수집합니다.
4. 소스별 성공·캐시 폴백 상태를 검증하고 Turso에 완성 스냅샷을 기록합니다.
5. `mail` 작업은 같은 KST 날짜의 완성 스냅샷만 읽습니다.
6. 5개 아카이브 저장소에 날짜 HTML·`archive.json`·`index.html`을 푸시합니다.
7. 날짜별 GitHub Pages URL의 게시 내용을 확인한 뒤 Gmail SMTP로 발송합니다.
8. SMTP `Message-ID`를 Turso와 `state/sent-YYYY-MM-DD.marker`에 기록합니다.

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
`ARCHIVE_PUSH_TOKEN`입니다. 기업마당에서 발급받은 `BIZINFO_API_KEY`를 추가하면
공식 JSON API만 사용합니다. 정기 작업은 Ubuntu에서 짧게 연결을 확인하고 실패하면
GitHub의 macOS 네트워크에서 재수집합니다. 두 경로가 모두 실패할 때만 캐시로
전환합니다. 수동 `scrape.yml`도 검증된 macOS 경로를 사용합니다. 키가 없는 로컬
환경에서만 공식 전체 엑셀 다운로드와 HTML 목록을 예비 경로로 사용합니다.
실제 비밀값은 GitHub Actions secrets에만 두며 러너 사이에는 키가 아니라 검증된
공개 API 응답만 전달합니다.

기존 기업마당 데이터의 API 요약·지원대상·해시태그·GPU·AI 라벨을 갱신하려면
`python -m src.cli --backfill-bizinfo-api`를 사용합니다.

수동 복구는 `scrape.yml`과 `mail.yml`의 `workflow_dispatch`로 실행하며, 정기
스케줄은 중복 방지를 위해 `daily.yml`에만 있습니다.

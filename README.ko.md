

# Monogram

**Language:** [English](README.md) · [한국어](README.ko.md)

> Telegram 공유와 GitHub 커밋이 위키 · 칸반 · 캘린더 · 일일 브리핑 · MCP · 암호화 대시보드로 자동 정리되는 개인 지식 파이프라인.

[![tests](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml)
[![eval](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

커밋은 자동으로 칸반프로젝트가 되고, 공유한 링크는 위키가 되어
아침에 캘린더 이벤트까지 포함한 데일리 리포트가 도착합니다. 세가지 뷰로
확인 — Obsidian, 대시보드, MCP

Telegram 저장된 메세지에 공유한 것 (유튜브/인스타링크, 메모,
PDF/word/hwp, 사진 등) 과 GitHub 에 커밋한 것을
Monogram 이 5-stage LLM pipeline 으로 분류하고 구조화된 마크다운으로
private GitHub 레포에 한 커밋으로 원자적으로 기록합니다. 이 마크다운는
GCP 위에서 자동 생성 · 암호화된 대시보드로 렌더링됩니다.

![Monogram 대시보드 — projects, wiki, life recent, commits](docs/images/dashboard.png)

다크, 정보 밀도 높은 UI, 비밀번호 보호, 클라이언트 사이드 복호화.
GCP 프리티어에 자동으로 호스팅되며 월 $0. 디자인 참고:
[docs/design/webui-mockup.html](docs/design/webui-mockup.html).

<video src="https://github.com/user-attachments/assets/fee5f42d-13d5-4897-b4e9-144947deb402" controls muted playsinline width="650"></video>

<!--
  ┌─────────────────────────────────────────────────────────────┐
  │  SHORT SLOT — replace the blockquote above with:            │
  │                                                             │
  │  Option A (inline GIF, autoplays on GitHub, ≤5 MB):         │
  │    ![30-second walkthrough](docs/images/short-demo.gif)     │
  │                                                             │
  │  Option B (clickable poster → YouTube Short):               │
  │    <a href="https://www.youtube.com/shorts/YOUR_ID">        │
  │      <img src="docs/images/short-poster.jpg"                │
  │           alt="30-second walkthrough" width="400"/>         │
  │    </a>                                                     │
  │                                                             │
  │  Option C (both — GIF inline + link to full Short):         │
  │    ![30-second walkthrough](docs/images/short-demo.gif)     │
  │                                                             │
  │    *Full walkthrough:                                       │
  │    [youtube.com/shorts/YOUR_ID](https://…)*                 │
  │                                                             │
  │  Target arc (15-30s):                                       │
  │    0-3s   phone: drop URL in Telegram Saved Messages        │
  │    3-10s  desktop: commit appears on GitHub                 │
  │   10-20s  browser: dashboard auto-updates with the drop     │
  │   20-30s  Claude Desktop: MCP query finds the same drop     │
  └─────────────────────────────────────────────────────────────┘
-->

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  INPUTS                                                      │
│    Telegram Saved Messages  ·  Obsidian plugin  ·  MCP       │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  PIPELINE     (5 stages · per-stage latency logged)          │
│    Orchestrator → Classifier → Extractor                     │
│                           → Verifier → Writer                │
└────────────────────────┬─────────────────────────────────────┘
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  마크다운  (git)                  BACKUP  (separate PAT)     │
│    <user>/mono          ⟶      <user>/mono-backup           │
└────────────────────────┬─────────────────────────────────────┘
                         │
       ┌─────────┬───────┴───────┬────────────┐
       ▼         ▼               ▼            ▼
   Morning    Weekly         Web UI       MCP server
    brief     rollup       (dashboard)  (Claude / Cursor)

┌──────────────────────────────────────────────────────────────┐
│  OBSERVABILITY         │  EVAL HARNESS       (optional)      │
│  log/pipeline.jsonl    │  cassette replay · harvest loop     │
│  /stats · CLI          │  3-layer kill-switch                │
└──────────────────────────────────────────────────────────────┘
```

여섯 개의 수평 레이어로 구성되어 있고, 입력 → 파이프라인 →
마크다운/백업 → 소비자 surface 순으로 이어집니다. 관측성과 eval 은
아래에서 cross-cutting 으로 걸쳐 있습니다. 상세 문서:
[docs/architecture.md](docs/architecture.md).

## Quickstart

Python 3.10+, GitHub 계정, Telegram 계정, LLM API 키 하나 (Gemini
프리티어로 충분함). 암호화 웹 대시보드를 GCS 에 올리려면 `gcloud`
CLI 를 설치하고 `gcloud auth login` 까지만 해두면 됩니다 — 나머지는
위저드가 처리합니다.

```bash
pip install mono-gram
monogram init            # interactive wizard — env · config · GCP 버킷까지 한번에
monogram auth            # one-time Telegram auth
monogram run             # listener + bot (leave running)
```

> ⚠️ **PyPI 승인 대기 중.** 아직 `pip install mono-gram` 으로는
> 받아지지 않습니다 —
> [docs/setup/install-from-source.md](docs/setup/install-from-source.md)
> 를 따라 소스에서 설치하세요:
>
> ```bash
> git clone https://github.com/HarimxChoi/monogram.git
> cd monogram
> python -m venv .venv && source .venv/bin/activate
> pip install -e .
> ```
>
> 이후 `monogram init`, `monogram run` 등 나머지 명령은 동일합니다.

> pip 패키지 이름은 `mono-gram`, CLI 명령어는 `monogram` 그대로이며
> Python import 경로도 `monogram` 입니다 — `from monogram import ...`.

Saved Messages 에 뭐든 보내면 몇 초 안에 마크다운 레포에 커밋이
업로드됩니다. 배포 end-to-end (GCP 프리티어 → PyPI):
**[deploying.md](deploying.md)**.

선택 확장:

```bash
pip install 'mono-gram[ingestion-all]'   # YouTube, arXiv, PDF, Office, HWP
pip install 'mono-gram[eval]'            # cassette-replay eval 하네스
```

## Web UI

하나의 마크다운로 3가지 방식의 대시보드 생성.

| 모드 | 실행 위치 | 언제 고르나 |
|---|---|---|
| **GCS** | 정적 버킷 + 클라이언트 사이드 복호화 | 기본값. 북마크 가능한 URL, 개인 규모에서 $0. 버킷 · 서비스 계정 · IAM 은 `monogram init` 이 `gcloud` 로 자동 프로비저닝. |
| **Self-host** | 로컬 Flask 또는 임의 정적 호스트 | 에어갭 / 사설 네트워크. |
| **MCP-only** | 웹 UI 없이 Claude Desktop / Cursor 로만 접근 | 터미널 중심 워크플로우. |

비밀번호로 보호되고 저장 시 암호화되며, 호스트에는 ciphertext 만
업로드됩니다. morning / weekly 실행마다 자동으로 재생성됩니다. 세팅:
[docs/webui.md](docs/webui.md) (~5분).

## 월 $0 로 운영

모든 단계가 무료 티어 안에서 동작하도록 설계했습니다:

- GCP `e2-micro` always-free VM 에서 리스너 + cron 상주
- GCS 프리티어로 암호화 대시보드 호스팅 — 버킷 · 서비스 계정 ·
  IAM 은 `monogram init` 이 `gcloud` CLI 로 자동 설정
- Gemini 프리티어로 LLM 파이프라인 전체 커버

**GPU 필요 없음.** 무료 LLM API 티어를 써도 되고, 로컬에서
Ollama 같은 모델을 연결해도 됩니다. 하드웨어 요구 자체가 없습니다.

**세팅 이후엔 PC 필요 없음.** 최초 설치 + `monogram init` +
Telegram 1회 인증까지만 데스크톱에서 진행하면, 그 뒤로는 VM 이
전부 처리합니다. 드롭은 휴대폰 → Telegram → 마크다운 → 대시보드로
자동화 됩니다.

## What you get

- **Single-commit atomic writes** — GitHub Git Tree API 로 드롭 하나가 한 커밋에 묶여 올라갑니다. 부분 상태 없음.
- **SSRF-hardened URL ingestion** — redirect 의 모든 hop 을 사전 검증하고, CGNAT 와 cloud metadata 범위까지 차단합니다.
- **Credential safety by construction** — classifier 단계 discriminator 와 verifier 게이트로 이중 차단.
- **Observability** — 드롭당 JSONL 한 줄이 남고, 필요할 때 p50/p95/p99 로 집계되며 Telegram `/stats` 로도 조회됩니다.
- **Backup isolation** — 별도 PAT 와 별도 레포를 쓰고, 월간 CI 가 복원 드릴까지 검증합니다.
- **LLM pluggability** — Gemini / Anthropic / OpenAI / Ollama / custom 을 tier 별로 자유롭게 섞어 씁니다.
- **Eval harness** — cassette replay 는 LLM 비용 0 이고, 기본 off 인 harvest loop 가 실드롭에서 fixture 를 늘려갑니다.
- **Kill-switch** — 독립 레이어 3개, first-match-wins.

각 항목은 [docs/](docs/) 에 별도 섹션으로 정리되어 있습니다.

## Commands

같은 마크다운에 대한 세 개의 surface — 상황에 맞는 걸 쓰세요.

**CLI** — `monogram --help` 에 전부 있고, 스테이지별 동작은
[docs/agents.md](docs/agents.md):

```
run · morning · weekly · digest · search · stats
backup · mcp-serve · eval · migrate
```

**Telegram 봇** — 휴대폰에서 on-demand 리포트 + 마크다운 쿼리. 모든 명령이
`TELEGRAM_USER_ID` 로 auth-gate 됩니다. 전체 레퍼런스:
[docs/setup/telegram.md §6 Bot commands](docs/setup/telegram.md#6-bot-commands).

```
/report  [YYYY-MM-DD]   모닝 브리핑 (기본: 어제)
/weekly  [YYYY-Www]     주간 리포트 (기본: 지난 월–일)
/digest  [Nh|Nd|Nw]     N 시간/일/주 동안의 커밋 다이제스트 (기본: 24h)
/search  <query>        고정 문자열 grep, credentials 경로는 차단
/last    [N]            최근 N 개 드롭 (기본 10, 최대 50)
/stats                  파이프라인 헬스 — log/pipeline.jsonl 의 p50/p95/p99
```

**MCP 서버** — Claude Desktop / Cursor / OpenClaw. reads + gated write +
LLM config 13개 툴. 세팅: [docs/setup/mcp-clients.md](docs/setup/mcp-clients.md),
전체 툴 목록은 [docs/mcp.md](docs/mcp.md).

## Ingestion

URL, PDF, Office 문서는 파이프라인이 보기 전에 텍스트로 추출됩니다.
전체 표와 폴백 체인은 [docs/ingestion.md](docs/ingestion.md) 참고.
HWP5 는 `pyhwp` 기반 — 순수 Python 이라 외부 바이너리 없음,
어택 표면이 좁습니다. 자세한 위협 모델은 [SECURITY.md](SECURITY.md) 참고.

## Credentials

Saved Messages 에 비밀번호나 API 키, 개인통관번호 같은 credential
을 올려두는 건 권장하는 저장 방식은 아닙니다. 다만 혹시 이렇게라도
보관해야 할 상황이 생겼을 때를 대비해 최대한 안전하게 처리됩니다.
분류기가 credential 로 인식하면 `life/credentials/` 로 격리되고,
이 경로는 코드 레벨에서 LLM 이 절대 읽지 못하도록 차단됩니다. 내용은
private GitHub 레포에만 남고, 본인은 Obsidian 으로 신뢰하는 기기에
동기화해서 꺼내 볼 수 있습니다.

## What this is *not*

- 챗봇이 아닙니다 — 대화형 turn-taking 은 지원하지 않습니다.
- 검색 엔진이 아닙니다 — `monogram search` 는 grep + scope 필터이고, 시맨틱 검색은 v1.1 에서 지원할 예정입니다.
- 멀티유저가 아닙니다 — Telegram 계정 하나, 마크다운 하나, 개인 지식 파이프라인을 전제로 설계됐습니다.
- Obsidian / Notion / Logseq 의 대체품이 아닙니다 — 어디까지나 수집 경로이고, 마크다운 자체는 어떤 마크다운 에디터에서도 그대로 열립니다.

## Roadmap

- **v0.8 (현재)** — core pipeline, ingestion, 하드닝, 관측성까지 갖춰져 있고, `mono-gram` 은 이미 PyPI 에 공개돼 있으며 dogfood 진행 중입니다.
- **v1.0** — dogfood 마무리 후 정식 tag cut, KAKAO Talk, Line, Whats app 지원.
- **v1.1** — 뉴스 다이제스트, MCP 클라이언트 모드, BM25 + embeddings / Graphify 기반 검색 추가.

출시된 기능 목록은 [CHANGELOG.md](CHANGELOG.md).

## Links

- [deploying.md](deploying.md) — GCP + GitHub + LLM provider 세팅, end-to-end
- [docs/architecture.md](docs/architecture.md) — 전체 토폴로지
- [docs/agents.md](docs/agents.md) — 스테이지별 스키마와 프롬프트
- [docs/setup/telegram.md](docs/setup/telegram.md) — Telegram API + 봇 세팅
- [docs/webui.md](docs/webui.md) — 대시보드 배포
- [docs/setup/llm-providers.md](docs/setup/llm-providers.md) — provider 프리셋
- [docs/setup/mcp-clients.md](docs/setup/mcp-clients.md) — Claude Desktop / Cursor 연동
- [docs/eval.md](docs/eval.md) — eval harness + kill-switch 설계
- [SECURITY.md](SECURITY.md) — threat model + disclosure

## License

MIT 라이선스입니다. 자세한 내용은 [LICENSE](LICENSE) 참고.

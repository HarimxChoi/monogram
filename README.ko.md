# Monogram

**Language:** [English](README.md) · [한국어](README.ko.md)

> Telegram 으로 던진다 → 위키로 쌓인다 → 아침엔 대시보드.

[![tests](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml)
[![eval](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Telegram Saved Messages 에 뭐든 보낸다 — 링크, 메모, PDF, 사진.
Monogram 이 5-stage LLM pipeline 으로 분류해서, 구조화된 마크다운으로
private GitHub 레포에 한 커밋에 담아 올린다. 그리고 볼트를 GCP 에
자동 생성·암호화된 대시보드로 렌더링한다.

커밋은 자동으로 Kanban 이 된다. 링크는 위키가 된다. 아침엔 브리핑이
도착한다. 볼트 하나, 뷰 세 개 — Obsidian, 대시보드, 그리고 MCP 로
연결된 Claude Desktop.

![Monogram 대시보드 — projects, wiki, life recent, commits](docs/images/dashboard.png)

다크, 정보 밀도 높음, 비밀번호 보호, 클라이언트 사이드 복호화.
static bucket ($0/월, GCS 프리티어), 자체 호스팅 서버, 아니면 아예
호스팅 없이 (MCP-only 모드) 돌릴 수 있다. 디자인 참고:
[docs/design/webui-mockup.html](docs/design/webui-mockup.html).

> 🎬 **30-second walkthrough** — 캡처 → 볼트 → 대시보드 → MCP 쿼리. *준비 중.*

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
│  VAULT  (git)                  BACKUP  (separate PAT)        │
│    <user>/mono          ⟶      <user>/mono-backup            │
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

여섯 수평 레이어. 입력 → 파이프라인 → 볼트/백업 → 소비자 surface.
관측성과 eval 은 아래에서 cross-cutting. 자세한 건
[docs/architecture.md](docs/architecture.md).

## Quickstart

Python 3.10+, GitHub 계정, Telegram 계정, LLM API 키 하나 (Gemini
프리티어면 충분).

```bash
pip install mono-gram
monogram init            # interactive wizard
monogram auth            # one-time Telegram auth
monogram run             # listener + bot (leave running)
```

> pip 패키지명은 `mono-gram`, CLI 와 Python import 경로는 모두
> `monogram` — `from monogram import ...`.

Saved Messages 에 뭔가 던지면 몇 초 안에 볼트 레포에 커밋이 올라온다.
End-to-end walkthrough (GCP 프리티어 → PyPI):
**[deploying.md](deploying.md)**.

Optional extras:

```bash
pip install 'mono-gram[ingestion-all]'   # YouTube, arXiv, PDF, Office, HWP
pip install 'mono-gram[eval]'            # cassette-replay eval harness
```

## Web UI

볼트 하나, 대시보드 배포 방식 세 개.

| 모드 | 실행 위치 | 언제 고르나 |
|---|---|---|
| **GCS** | 정적 버킷, 클라이언트 사이드 복호화 | 기본값. 북마크 가능한 URL, 개인 규모에선 $0. |
| **Self-host** | 로컬 Flask 또는 임의 정적 호스트 | 에어갭 / 사설 네트워크. |
| **MCP-only** | 웹 인터페이스 없음 — Claude Desktop / Cursor 로만 접근 | 터미널 중심 워크플로우. |

비밀번호 보호, 저장 시 암호화, 호스트에는 ciphertext 만 올라간다.
morning / weekly 실행 때 재생성. 세팅:
[docs/setup/gcp-webui.md](docs/setup/gcp-webui.md) (~5분).

## What you get

- **Single-commit atomic writes** — GitHub Git Tree API 기반. 부분 상태 없음.
- **SSRF-hardened URL ingestion** — 모든 redirect hop 검증, CGNAT 와 cloud metadata 범위까지 포함.
- **Credential safety by construction** — classifier 단계 discriminator + verifier 게이트.
- **Observability** — 드롭당 JSONL 한 줄. 필요할 때 p50/p95/p99 집계. Telegram `/stats` 로 조회.
- **Backup isolation** — 별도 PAT + CI 의 월간 복원 드릴.
- **LLM pluggability** — Gemini / Anthropic / OpenAI / Ollama / custom, tier 별 지정.
- **Eval harness** — cassette replay (LLM 비용 0), 실드롭에서 fixture 자라는 harvest loop (기본 off).
- **Kill-switch** — 독립 레이어 3개, first-match-wins.

각 항목은 [docs/](docs/) 에 짧은 섹션.

## Commands

```
run · morning · weekly · digest · search · stats
backup · mcp-serve · eval · migrate
```

`monogram --help` 또는 [docs/agents.md](docs/agents.md).

## Ingestion

URL, PDF, Office 문서 — 파이프라인이 보기 전에 추출된다. 전체 표와
폴백 체인은 [docs/ingestion.md](docs/ingestion.md). HWP 는
CVE-2024-12425/12426, CVE-2025-1080 에 대해 하드닝.
[SECURITY.md](SECURITY.md) 참고.

## What this is *not*

- 챗봇 아니다 — 대화형 turn-taking 없음.
- 검색 엔진 아니다 — `monogram search` 는 grep + scope 필터. 시맨틱 검색은 v1.1.
- 멀티유저 아니다 — Telegram 계정 하나, 볼트 하나, 사람 한 명.
- Obsidian / Notion / Logseq 의 대체품 아니다 — 수집 경로일 뿐. 볼트는 어떤 마크다운 에디터에서도 그대로 열린다.

## Roadmap

- **v0.8 (현재)** — core pipeline, ingestion, 하드닝, 관측성. `mono-gram` 은 이미 PyPI 에 있고 dogfood 진행 중.
- **v1.0** — dogfood 마무리 후 tag cut.
- **v1.1** — 뉴스 다이제스트, MCP 클라이언트 모드, BM25 + embeddings 검색.

출하된 기능은 CHANGELOG.md 참고.

## Links

- [deploying.md](deploying.md) — GCP + GitHub + LLM provider 세팅, end-to-end
- [docs/architecture.md](docs/architecture.md) — 전체 토폴로지
- [docs/agents.md](docs/agents.md) — 스테이지별 스키마와 프롬프트
- [docs/setup/gcp-webui.md](docs/setup/gcp-webui.md) — 대시보드 배포
- [docs/setup/llm-providers.md](docs/setup/llm-providers.md) — provider 프리셋
- [docs/setup/mcp-clients.md](docs/setup/mcp-clients.md) — Claude Desktop / Cursor 연동
- [docs/eval.md](docs/eval.md) — eval harness + kill-switch 설계
- [SECURITY.md](SECURITY.md) — threat model + disclosure

## License

MIT. [LICENSE](LICENSE) 참고.

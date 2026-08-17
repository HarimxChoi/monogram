# Monogram

[English](./README.md) | 한국어

> 한 번 공유하면 뉴스·SNS·논문·영상·문서가 출처와 함께 정리되고, 다시 검색할 수 있는 지식이 됩니다.

[![tests](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/tests.yml)
[![eval](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml/badge.svg)](https://github.com/HarimxChoi/monogram/actions/workflows/eval.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

## Why

뉴스, SNS, arXiv, YouTube, 리포트와 문서에서 본 정보를 북마크만 해두면 왜 저장했는지 기억하기 어렵고, 서비스마다 흩어진 내용을 프로젝트에 다시 활용하기도 어려웠습니다. 형식에 관계없이 한 번 공유한 정보를 자동으로 정리하고 나중에 검색할 수 있는 개인 지식관리 시스템이 필요했습니다.

## How

Telegram Saved Messages, Obsidian quick capture와 MCP를 통해 링크, 메시지, 문서와 코드를 수집합니다. **Orchestrator → Classifier → Extractor → Verifier → Writer**의 5단계 파이프라인이 출처와 metadata를 추출해 Markdown으로 구조화하고, 한 번의 수집에서 바뀐 파일을 Git Tree API의 단일 commit으로 저장합니다. Git에는 원문과 변경 이력이 남고, Dashboard와 MCP는 같은 vault를 사용자와 Agent에게 보여줍니다.

## Result

뉴스·SNS·논문·영상과 문서를 별도로 옮겨 적지 않아도 공유 한 번으로 검색 가능하고 변경 이력이 남는 지식 workflow를 구현했습니다. 지식은 일반 Markdown으로 남고, 암호화된 Dashboard에서 확인할 수 있으며, **13개 MCP tool**로 프로젝트 상태·브리핑·검색과 승인 기반 쓰기 작업을 다른 Agent에 제공합니다.

![Monogram dashboard — projects, wiki, life recent, commits](docs/images/dashboard.png)

![Monogram walkthrough — capture, vault, dashboard, MCP](docs/images/short-demo.gif)

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

6개 horizontal plane. Inputs → pipeline → vault/backup → consumer surface.
Observability와 eval은 아래에서 cross-cutting. 전체 설명:
[docs/architecture.md](docs/architecture.md).

## Quickstart

Python 3.10+, GitHub 계정, Telegram 계정, LLM API key 1개 (Gemini 무료 티어로 충분).

```bash
pip install mono-gram
monogram init            # interactive wizard
monogram auth            # 1회 Telegram 인증
monogram run             # listener + bot (계속 실행)
```

> pip 패키지명은 `mono-gram`, CLI 명령어는 `monogram` 유지.
> Python import도 `monogram`. `from monogram import ...`.

Saved Messages에 아무거나 하나 보내기. 몇 초 안에 vault repo에 commit이 올라옴.
End-to-end walkthrough (GCP 무료 티어 → PyPI): **[deploying.md](deploying.md)**.

선택 extras:

```bash
pip install 'mono-gram[ingestion-all]'   # YouTube, arXiv, PDF, Office, HWP
pip install 'mono-gram[eval]'            # cassette-replay eval harness
```

## Web UI

Vault 1개, 대시보드 배포 방식 3가지:

| Mode | 실행 위치 | 언제 고를지 |
|---|---|---|
| **GCS** | Static bucket, 클라이언트 사이드 복호화 | 기본. Bookmarkable URL, 개인 규모 $0. |
| **Self-host** | 로컬 Flask 또는 static host | Air-gapped / private network. |
| **MCP-only** | Web 없음, Claude Desktop / Cursor로 접근 | 터미널 중심 워크플로. |

비밀번호 보호. 컨텐츠는 at rest 암호화, 호스트는 ciphertext만 보관.
Morning / weekly run 때 재생성. 설정: [docs/setup/gcp-webui.md](docs/setup/gcp-webui.md) (~5분).

## What you get

- **원자적 ingest commit**: 한 번의 수집에서 만들어진 모든 경로를 GitHub Git Tree API의 단일 commit으로 저장.
- **하이브리드·그래프 검색**: local embedding, BM25/RRF와 event graph로 같은 Markdown vault를 검색하며 chat model provider에 종속되지 않음.
- **13개 MCP tool**: 프로젝트 상태·지식·브리핑 조회와 쓰기 요청 제공. 민감한 쓰기 tool은 Telegram 승인이 필요.
- **SSRF-hardened URL ingestion**: 모든 hop 검증, CGNAT + cloud metadata range 포함.
- **Credential safety by construction**: classifier 라우팅 + writer 단계의 secret-shape redaction backstop.
- **Observability**: run당 JSONL 1줄, p50/p95/p99 on demand, Telegram `/stats`.
- **Backup isolation**: 별도 PAT + CI에서 월 1회 restore drill.
- **LLM pluggability**: Gemini / Anthropic / OpenAI / Ollama / custom, tier별.
- **Eval harness**: cassette replay (LLM 비용 0), harvest loop (기본 off)로 실제 drop에서 fixture 키움.
- **Kill-switch**: 독립적 3개 layer, first match wins.

각 항목은 [docs/](docs/)에 짧은 섹션으로 있음.

## Commands

```
run · morning · weekly · digest · search · stats
backup · mcp-serve · eval · migrate
```

상세: `monogram --help` 또는 [docs/agents.md](docs/agents.md).

## Ingestion

URL, PDF, Office doc 보내면 파이프라인 들어가기 전에 추출됨. 전체 표 +
fallback chain: [docs/ingestion.md](docs/ingestion.md). HWP는
CVE-2024-12425/12426, CVE-2025-1080 hardening 적용. [SECURITY.md](SECURITY.md) 참고.

## What this is *not*

- 챗봇 아님. 대화 turn-taking 없음.
- 일반 웹 검색 엔진 아님. 검색 범위는 사용자가 저장한 vault로 한정.
- Multi-user 아님. Telegram 계정 1개, vault 1개, 사람 1명.
- Obsidian/Notion/Logseq 대체 아님. Ingest path임. Vault는 어떤 markdown 에디터에서도 네이티브 렌더링.

## Links

- [deploying.md](deploying.md): GCP + GitHub + LLM provider 셋업, end-to-end
- [docs/architecture.md](docs/architecture.md): 전체 topology
- [docs/agents.md](docs/agents.md): stage별 스키마 + 프롬프트
- [docs/setup/gcp-webui.md](docs/setup/gcp-webui.md): 대시보드 배포
- [docs/setup/llm-providers.md](docs/setup/llm-providers.md): provider preset config
- [docs/setup/mcp-clients.md](docs/setup/mcp-clients.md): Claude Desktop / Cursor 연동
- [docs/eval.md](docs/eval.md): eval harness + kill-switch 설계
- [SECURITY.md](SECURITY.md): threat model + 제보
- [CONTRIBUTING.md](CONTRIBUTING.md): 기여 방법

## License

MIT. [LICENSE](LICENSE) 참고.

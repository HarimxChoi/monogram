# Install from source (PyPI pending)

> ⚠️  **PyPI is still pending approval.** `mono-gram` is reserved via
> GitHub Trusted Publishing (`HarimxChoi/monogram` → `publish.yml` →
> `pypi` environment) but the first release hasn't been accepted yet,
> so `pip install mono-gram` won't work. Use the steps below until the
> package is live; the rest of the docs match the post-PyPI flow.

## Prerequisites

- Python ≥ 3.10
- `git`
- (optional) `gcloud` CLI — only if you want the encrypted GCS web UI;
  see [install.md](install.md) for the full prereq list.

## Clone + editable install

```bash
git clone https://github.com/HarimxChoi/monogram.git
cd monogram
python -m venv .venv && source .venv/bin/activate   # or conda, uv, ...
pip install -e .
```

That gives you the `monogram` CLI entry point and the `monogram`
Python package. The editable install lets you pull new commits with
`git pull` and pick them up without reinstalling.

### Optional extras

Match the same extras as the future PyPI package:

```bash
pip install -e '.[ingestion-all]'   # YouTube, arXiv, PDF, Office, HWP
pip install -e '.[eval]'            # cassette-replay eval harness
pip install -e '.[ingestion-all,eval]'
```

## Continue the setup

Once the install finishes, the rest is identical to the PyPI path:

```bash
monogram init            # interactive wizard — env · config · GCP bucket inline
monogram auth            # one-time Telegram auth
monogram run             # listener + bot (leave running)
```

Wizard details and prerequisites (`gcloud`, GitHub PAT, Telegram API
creds, LLM key) live in [install.md](install.md).

## When PyPI approval lands

The published package will be installable with `pip install mono-gram`
and the [`README.md`](../../README.md) / [`README.ko.md`](../../README.ko.md)
quickstart blocks apply verbatim. This doc stays as the fallback for
contributors and anyone running pre-release builds.

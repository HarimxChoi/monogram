import subprocess
import sys


def test_version():
    import re

    import monogram

    # __version__ is read from package metadata (see src/monogram/__init__.py).
    # Assert it's a recognisable version string rather than a hardcoded value
    # so the test survives version bumps without being touched.
    v = monogram.__version__
    assert v and v != "0.0.0+unknown", f"unexpected version {v!r}"
    assert re.match(r"^\d+\.\d+(\.\d+)?([a-z0-9.+-]*)?$", v), (
        f"version {v!r} does not look like a PEP 440 string"
    )


def test_cli_module_importable():
    from monogram import cli

    assert callable(cli.main)


def test_cli_help_runs():
    result = subprocess.run(
        [sys.executable, "-m", "monogram", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "monogram" in result.stdout.lower()


def test_console_script_help():
    result = subprocess.run(
        ["monogram", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "monogram" in result.stdout.lower()


def test_cli_exposes_subcommands():
    result = subprocess.run(
        ["monogram", "--help"], capture_output=True, text=True
    )
    for cmd in ("init", "mcp-serve", "run"):
        assert cmd in result.stdout, f"missing subcommand {cmd!r} in --help"


def test_init_help_exists():
    """v0.3b: init is now an interactive wizard — just check --help works."""
    result = subprocess.run(
        ["monogram", "init", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "GitHub" in result.stdout or "language" in result.stdout or "init" in result.stdout


def test_run_command_exists():
    result = subprocess.run(["monogram", "--help"], capture_output=True, text=True)
    assert "run" in result.stdout

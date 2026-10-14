"""Publication-gate regressions using isolated source trees and Git repositories."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "privacy_check.py"
SPEC = importlib.util.spec_from_file_location("nightdesk_privacy_check", MODULE_PATH)
privacy = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = privacy
SPEC.loader.exec_module(privacy)


def rules(entries, terms=()):
    return {finding.rule for finding in privacy.scan_entries(entries, terms)[1]}


def test_generated_account_model_and_unknown_formats_are_blocked():
    entries = [
        privacy.Entry("data/" + "state/account.json", b"{}"),
        privacy.Entry("models/weights.pkl", b"model"),
        privacy.Entry("archive.zip", b"archive"),
        privacy.Entry("docs/diagram.png", b"\x89PNG\0"),
    ]
    assert {"runtime-artifact", "private-artifact", "unreviewed-file-type", "binary-content"} <= rules(entries)


def test_credentials_paths_and_private_audit_terms_are_redacted():
    generated = "gh" + "p_" + "A" * 36
    local_path = "/" + "Users/" + "private-owner/project/"
    credential_line = "api" + "_key = " + '"real-private-value"'
    text = "\n".join([generated, local_path, credential_line, "PRIVATE IDENTIFIER"])
    _, findings = privacy.scan_entries([privacy.Entry("app.py", text.encode())], ["private identifier"])
    assert {"github-token", "local-home-path", "literal-credential", "private-audit-term"} <= {f.rule for f in findings}
    rendered = json.dumps([privacy.asdict(f) for f in findings])
    for confidential in (generated, local_path, "real-private-value", "PRIVATE IDENTIFIER"):
        assert confidential not in rendered


def test_clearly_marked_examples_and_environment_reads_are_allowed():
    text = '\n'.join([
        'to' + 'ken = "<YOUR_' + 'TOKEN>"',
        'password = ""',
        'api' + '_key = os.environ.get("SERVICE_KEY")',
        'contact: someone@example.com',
    ])
    assert not rules([privacy.Entry(".env.example", text.encode())])


def test_private_keys_embedded_in_source_are_blocked():
    text = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\npretend-key"
    assert "private-key" in rules([privacy.Entry("config.py", text.encode())])


def test_generic_secret_names_and_private_filenames_are_redacted():
    content = ('AWS_' + 'SECRET_ACCESS_' + 'KEY' + ' = ' + '"private-value"').encode()
    _, findings = privacy.scan_entries([privacy.Entry("private-person.md", content)], ["private-person"])
    assert "literal-credential" in {f.rule for f in findings}
    assert "private-audit-term" in {f.rule for f in findings}
    assert "private-person" not in json.dumps([privacy.asdict(f) for f in findings])


def test_unquoted_dotenv_credentials_are_blocked_but_placeholders_pass():
    key = "API" + "_KEY"
    assert "literal-credential" in rules([privacy.Entry(".env.example", (key + "=private-service-value").encode())])
    assert not rules([privacy.Entry(".env.example", (key + "=<YOUR_KEY>").encode())])


def test_symlink_and_manifest_escape_never_read_target(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private content")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "link.py").symlink_to(outside)
    entries = privacy.working_entries(root, ["link.py", "../outside.txt", "C:/outside.txt"])
    assert all(entry.data is None for entry in entries)
    assert {"special-file", "unsafe-path", "unreadable-file"} <= rules(entries)


def test_unreadable_empty_and_non_utf8_fail_closed():
    assert "empty-source" in rules([])
    assert "unreadable-file" in rules([privacy.Entry("missing.py", None)])
    assert "non-utf8-content" in rules([privacy.Entry("app.py", b"\xff")])


def test_runtime_directories_and_large_files_are_rejected_without_loading_them(tmp_path):
    hidden = tmp_path / ".venv" / "nested"
    hidden.mkdir(parents=True)
    (hidden / "private.py").write_text("private content")
    metadata = tmp_path / "package.egg-info"
    metadata.mkdir()
    (metadata / "requires.txt").write_text("numpy\n")
    large = tmp_path / "large.txt"
    with large.open("wb") as stream:
        stream.truncate(privacy.MAX_BYTES + 1)
    entries = privacy.working_entries(tmp_path)
    assert len(entries) == 3
    assert all(entry.data is None for entry in entries)
    assert {"runtime-artifact", "oversized-file"} <= rules(entries)


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, check=True)
    return result.stdout


@pytest.fixture
def repository(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Test Author")
    git(tmp_path, "config", "user.email", "author@example.com")
    return tmp_path


def test_index_scan_catches_secret_even_after_working_file_is_cleaned(repository):
    generated = "hf" + "_" + "B" * 30
    path = repository / "app.py"
    path.write_text(generated)
    git(repository, "add", "app.py")
    path.write_text("print('clean')\n")
    assert "service-token" in rules(privacy.index_entries(repository))
    assert not rules(privacy.working_entries(repository))


def test_history_scan_catches_removed_secret(repository):
    generated = "gh" + "p_" + "C" * 36
    path = repository / "app.py"
    path.write_text(generated)
    git(repository, "add", "app.py")
    git(repository, "commit", "-qm", "Add source")
    path.write_text("print('clean')\n")
    git(repository, "add", "app.py")
    git(repository, "commit", "-qm", "Clean source")
    assert not rules(privacy.index_entries(repository))
    count, findings = privacy.history_findings(repository, ())
    assert count == 2
    assert "history:github-token" in {f.rule for f in findings}


def test_history_commit_metadata_is_inspected(repository):
    (repository / "app.py").write_text("print('clean')\n")
    git(repository, "add", "app.py")
    git(repository, "commit", "-qm", "Private project identifier")
    _, findings = privacy.history_findings(repository, ["private project identifier"])
    assert any(f.path.startswith("<commit:") and f.rule == "private-audit-term" for f in findings)


def test_cli_inspection_failure_returns_error_without_private_output(tmp_path, capsys):
    assert privacy.main(["--root", str(tmp_path / "missing"), "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["passed"] is False
    assert result["findings"][0]["rule"] == "inspection-error"
    assert str(tmp_path) not in json.dumps(result)


def test_manifest_is_explicit_and_duplicates_are_errors(tmp_path):
    (tmp_path / "app.py").write_text("print('clean')\n")
    (tmp_path / "local.env").write_text("private runtime state")
    manifest = tmp_path / "publish.txt"
    manifest.write_text("# reviewed source\napp.py\n")
    assert not rules(privacy.working_entries(tmp_path, privacy.manifest_paths(manifest)))
    manifest.write_text("app.py\napp.py\n")
    with pytest.raises(ValueError):
        privacy.manifest_paths(manifest)

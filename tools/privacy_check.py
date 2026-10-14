#!/usr/bin/env python3
"""Fail-closed publication checks for Nightdesk source and Git history.

Run ``python tools/privacy_check.py --history`` immediately before a push.
In a Git checkout the default checks the *index contents*, including files that
have not been committed. Without Git it checks the whole source tree. ``--tree``
checks working files, and ``--manifest PATH`` checks an explicit UTF-8 file list.
Private audit terms can be supplied through ``--deny-text-file`` outside the
repository (one term per line). Findings never reproduce matched content.

This is a publication gate, not proof that arbitrary text contains no private
information. Review source and the final file list as well. Runtime files,
unknown file types, symlinks, submodules, unreadable content, and scanning errors
are rejected rather than silently skipped.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Iterable


MAX_BYTES = 2_000_000
TEXT_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".css", ".html",
    ".md", ".txt", ".ini", ".toml", ".yaml", ".yml", ".json", ".sh",
    ".swift", ".svg", ".lock", ".example", ".cfg", ".rst", ".command",
}
TEXT_NAMES = {
    "LICENSE", "NOTICE", "Dockerfile", "Makefile", ".gitignore", ".gitattributes",
    ".editorconfig", ".python-version", ".npmrc", "MANIFEST.in",
}
FORBIDDEN_DIRS = {
    "data", "state", "cache", "models", "logs", "memory", ".claude",
    ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "build", ".build", "dist", ".ssh", ".aws",
}
FORBIDDEN_NAMES = {
    ".DS_Store", "account.json", "paper_account.json", "settings.json",
    "orders.json", "fills.json", "trades.json", "ledger.json", "resources.json",
    "reader_resources.json", "credentials", "credentials.json", "id_rsa", "id_ed25519",
}

# These patterns deliberately contain no real credential or owner identity.
PATTERNS = [
    ("private-key", re.compile(r"-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----")),
    ("cloud-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("service-token", re.compile(r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b")),
    ("jwt-token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("local-home-path", re.compile(r"/(?:Users|home)/[A-Za-z0-9_. -]+(?:/|\\)", re.IGNORECASE)),
    ("local-volume-path", re.compile(r"\/Volumes\/[^\s'\"<>]+", re.IGNORECASE)),
    ("windows-home-path", re.compile(r"\b[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\s'\"<>]+", re.IGNORECASE)),
    ("email-address", re.compile(r"\b[A-Za-z0-9][A-Za-z0-9._%+-]*@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")),
]
SECRET_ASSIGNMENT = re.compile(
    r"(?i)[\"']?\b(?:[A-Za-z0-9_]*(?:api[_-]?(?:key|token)|auth[_-]?(?:key|token)|"
    r"secret(?:[_-]?(?:key|access[_-]?key))?|access[_-]?(?:key|token)|refresh[_-]?token|"
    r"password|passwd|credentials?|private[_-]?key)|token)"
    r"\b[\"']?\s*[:=]\s*([\"'])([^\r\n]*?)\1"
)
UNQUOTED_SECRET = re.compile(
    r"(?i)^\s*(?:export\s+)?(?:[A-Za-z0-9_]*(?:api[_-]?(?:key|token)|auth[_-]?(?:key|token)|"
    r"secret(?:[_-]?(?:key|access[_-]?key))?|access[_-]?(?:key|token)|refresh[_-]?token|"
    r"password|passwd|credentials?|private[_-]?key)|token)\s*[:=]\s*"
    r"([A-Za-z0-9_+/.=<>${}-]+)\s*(?:#.*)?$"
)
AUTHORIZATION = re.compile(r"(?i)\b(?:Bearer|Basic)\s+([A-Za-z0-9_+/.=-]{12,})")
URL_CREDENTIAL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://([^\s/:]+):([^\s/@]+)@")
SECRET_QUERY = re.compile(r"(?i)[?&](?:api[_-]?key|access[_-]?token|token|password|secret)=([^&#\s'\"]+)")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str


@dataclass(frozen=True)
class Entry:
    path: str
    data: bytes | None
    mode: str = "100644"
    error: str | None = None


def placeholder(value: str) -> bool:
    """Only accept clearly empty or deliberately marked examples."""
    value = value.strip()
    if not value or value.lower() in {"none", "null", "example", "placeholder", "not-configured"}:
        return True
    return bool(re.fullmatch(
        r"(?:\$\{[A-Z0-9_]+\}|<[A-Z0-9_ -]+>|(?:YOUR|REPLACE|EXAMPLE|PLACEHOLDER)[_-][A-Z0-9_-]+)",
        value,
    ))


def path_findings(path: str, mode: str) -> list[Finding]:
    pure = PurePosixPath(path)
    issues = []
    if not safe_relative_path(path):
        issues.append(Finding(path, 0, "unsafe-path", "Publication paths must be relative and stay inside the repository."))
    if mode not in {"100644", "100755"}:
        issues.append(Finding(path, 0, "special-file", "Symlinks, submodules, and special files are not publishable."))
    if any(forbidden_directory(part) for part in pure.parts[:-1]) or (mode == "040000" and forbidden_directory(pure.name)):
        issues.append(Finding(path, 0, "runtime-artifact", "Runtime state, downloaded data, models, or generated artifacts are not publishable."))
    if pure.name in FORBIDDEN_NAMES or (pure.name.startswith(".env") and pure.name != ".env.example"):
        issues.append(Finding(path, 0, "private-artifact", "This file name denotes local account, machine, or credential state."))
    if pure.suffix.lower() not in TEXT_SUFFIXES and pure.name not in TEXT_NAMES:
        issues.append(Finding(path, 0, "unreviewed-file-type", "Only explicitly reviewed text source formats are permitted."))
    return issues


def safe_relative_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return not (pure.is_absolute() or ".." in pure.parts or "\\" in path or "\x00" in path
                or re.match(r"^[A-Za-z]:", path))


def forbidden_directory(name: str) -> bool:
    return name.lower() in FORBIDDEN_DIRS or name.lower().endswith((".egg-info", ".app"))


def scan_text(path: str, text: str, deny_terms: Iterable[str] = ()) -> list[Finding]:
    issues: list[Finding] = []
    terms = tuple(term.casefold() for term in deny_terms if term.strip())
    for number, line in enumerate(text.splitlines(), 1):
        folded = line.casefold()
        if any(term in folded for term in terms):
            issues.append(Finding(path, number, "private-audit-term", "A privately supplied audit term matched; content is redacted."))
        for rule, pattern in PATTERNS:
            for match in pattern.finditer(line):
                if rule == "email-address" and match.group(1).lower() in {
                    "example.com", "example.org", "example.net", "users.noreply.github.com",
                }:
                    continue
                issues.append(Finding(path, number, rule, "Potential private content found; matched content is redacted."))
                break
        candidates = [m.group(2) for m in SECRET_ASSIGNMENT.finditer(line)]
        candidates.extend(m.group(1) for m in UNQUOTED_SECRET.finditer(line)
                          if m.group(1).lower() not in {"none", "null", "true", "false"})
        candidates.extend(m.group(1) for m in AUTHORIZATION.finditer(line))
        candidates.extend(m.group(2) for m in URL_CREDENTIAL.finditer(line))
        candidates.extend(m.group(1) for m in SECRET_QUERY.finditer(line))
        if any(not placeholder(value) for value in candidates):
            issues.append(Finding(path, number, "literal-credential", "A credential-like literal must be removed or replaced by a clear placeholder."))
    return issues


def scan_entries(entries: Iterable[Entry], deny_terms: Iterable[str] = ()) -> tuple[int, list[Finding]]:
    deny_terms = tuple(deny_terms)
    findings = []
    count = 0
    for entry in entries:
        count += 1
        findings.extend(path_findings(entry.path, entry.mode))
        for finding in scan_text(entry.path, entry.path, deny_terms):
            findings.append(Finding(entry.path, 0, finding.rule, finding.detail))
        if entry.error == "excluded-runtime-directory":
            continue
        if entry.error == "oversized-file":
            findings.append(Finding(entry.path, 0, "oversized-file", "File exceeds the publication review size limit."))
            continue
        if entry.error or entry.data is None:
            findings.append(Finding(entry.path, 0, "unreadable-file", "File contents could not be inspected."))
            continue
        if len(entry.data) > MAX_BYTES:
            findings.append(Finding(entry.path, 0, "oversized-file", "File exceeds the publication review size limit."))
            continue
        if b"\0" in entry.data:
            findings.append(Finding(entry.path, 0, "binary-content", "Binary content cannot pass the source privacy gate."))
            continue
        try:
            text = entry.data.decode("utf-8")
        except UnicodeError:
            findings.append(Finding(entry.path, 0, "non-utf8-content", "Only inspectable UTF-8 source is publishable."))
            continue
        findings.extend(scan_text(entry.path, text, deny_terms))
    if not count:
        findings.append(Finding("<source>", 0, "empty-source", "No publication files were selected."))
    return count, [Finding(redact_path(f.path, deny_terms), f.line, f.rule, f.detail) for f in findings]


def redact_path(path: str, deny_terms: Iterable[str]) -> str:
    """A confidential filename must not leak through an otherwise redacted report."""
    for _, pattern in PATTERNS:
        path = pattern.sub("<redacted>", path)
    for term in deny_terms:
        if term.strip():
            path = re.sub(re.escape(term), "<redacted>", path, flags=re.IGNORECASE)
    return path


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        # Git's error message can contain paths or content. Do not echo it.
        raise RuntimeError("Git inspection failed; no publication approval was issued.")
    return result.stdout


def index_entries(root: Path) -> list[Entry]:
    entries = []
    for record in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8")
        if stage != "0":
            entries.append(Entry(path, None, mode, "unmerged-index"))
        else:
            entries.append(blob_entry(root, path, mode, object_id))
    return entries


def blob_entry(root: Path, path: str, mode: str, object_id: str) -> Entry:
    if int(git(root, "cat-file", "-s", object_id)) > MAX_BYTES:
        return Entry(path, None, mode, "oversized-file")
    return Entry(path, git(root, "cat-file", "blob", object_id), mode)


def working_entries(root: Path, paths: Iterable[str] | None = None) -> list[Entry]:
    entries = []
    if paths is None:
        selected = []
        for directory, directories, files in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if name != ".git"]
            for name in list(directories):
                p = Path(directory) / name
                if p.is_symlink():
                    selected.append(p.relative_to(root).as_posix())
                    directories.remove(name)
                elif forbidden_directory(name):
                    entries.append(Entry(p.relative_to(root).as_posix() + "/", None, "040000", "excluded-runtime-directory"))
                    directories.remove(name)
            selected.extend((Path(directory) / name).relative_to(root).as_posix() for name in files)
        paths = selected
    for path in sorted(paths):
        p = root / path
        if not safe_relative_path(path):
            entries.append(Entry(path, None, error="unsafe-path"))
            continue
        try:
            info = p.lstat()
            if stat.S_ISLNK(info.st_mode) or any(parent.is_symlink() for parent in p.parents if parent != root and root in parent.parents):
                entries.append(Entry(path, None, "120000", "symlink"))
            elif not stat.S_ISREG(info.st_mode):
                entries.append(Entry(path, None, "special", "not-regular"))
            elif info.st_size > MAX_BYTES:
                entries.append(Entry(path, None, error="oversized-file"))
            else:
                entries.append(Entry(path, p.read_bytes(), "100755" if info.st_mode & 0o111 else "100644"))
        except OSError:
            entries.append(Entry(path, None, error="unreadable"))
    return entries


def manifest_paths(path: Path) -> list[str]:
    paths = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            if line in paths:
                raise ValueError("Publication manifest contains a duplicate entry.")
            paths.append(line)
    return paths


def history_findings(root: Path, deny_terms: Iterable[str]) -> tuple[int, list[Finding]]:
    findings = []
    seen = set()
    commits = git(root, "rev-list", "--all").decode("ascii").splitlines()
    count = 0
    for commit in commits:
        metadata = git(root, "show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", commit).decode("utf-8")
        findings.extend(scan_text("<commit:" + commit[:12] + ">", metadata, deny_terms))
        entries = []
        for record in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
            if not record:
                continue
            header, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = header.decode("ascii").split()
            path = raw_path.decode("utf-8")
            key = (path, mode, object_id)
            if key in seen:
                continue
            seen.add(key)
            entries.append(blob_entry(root, path, mode, object_id) if kind == "blob" else Entry(path, None, mode, "not-blob"))
        if entries:
            scanned, issues = scan_entries(entries, deny_terms)
            count += scanned
            for finding in issues:
                findings.append(Finding(finding.path, finding.line, "history:" + finding.rule, finding.detail))
    return count, findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--tree", "--working-tree", action="store_true", help="Inspect the full working tree, including ignored files.")
    selection.add_argument("--manifest", type=Path, help="Inspect exactly the paths in this source manifest.")
    parser.add_argument("--history", action="store_true", help="Also inspect every reachable Git revision and commit metadata.")
    parser.add_argument("--deny-text-file", type=Path, help="Private UTF-8 audit terms, kept outside this repository.")
    parser.add_argument("--forbid", "--deny-text", action="append", default=[], help="Additional private audit term (repeatable; never echoed in findings).")
    parser.add_argument("--json", action="store_true", help="Emit redacted JSON findings.")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve(strict=True)
        terms = list(args.deny_text_file.read_text(encoding="utf-8").splitlines() if args.deny_text_file else ()) + args.forbid
        if args.manifest:
            entries = working_entries(root, manifest_paths(args.manifest))
            source = "manifest"
        elif args.tree or not (root / ".git").exists():
            entries = working_entries(root)
            source = "working tree"
        else:
            entries = index_entries(root)
            source = "Git index"
        count, findings = scan_entries(entries, terms)
        history_count = 0
        if args.history:
            history_count, historical = history_findings(root, terms)
            findings.extend(historical)
    except (OSError, UnicodeError, ValueError, RuntimeError, subprocess.SubprocessError):
        count, history_count, source = 0, 0, "unavailable"
        findings = [Finding("<scanner>", 0, "inspection-error", "Inspection was incomplete; publication is blocked. No content was echoed.")]
    if args.json:
        print(json.dumps({"passed": not findings, "source": source, "files_scanned": count,
                          "historical_files_scanned": history_count, "findings": [asdict(f) for f in findings]}, indent=2))
    else:
        for finding in findings:
            print(f"{finding.path}:{finding.line}: {finding.rule}: {finding.detail}")
        print(f"{'FAIL' if findings else 'PASS'}: inspected {count} source files ({source}) and {history_count} historical file versions.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

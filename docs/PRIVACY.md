# Privacy and publication

The public repository is a source-only export. It contains app code, installation scripts, tests, clean educational material and reviewed historical simulation summaries.

Excluded: original account databases and settings, positions and fills, logs and activity records, downloaded price caches, model weights, private handover and memory files, screenshots of accounts, prebuilt apps and videos, hardware measurements, unrelated installed software inventory, credentials and local machine paths.

## Runtime behavior

First launch is offline and practice trading is paused. Only explicit user consent enables public-data downloads. The app uses Yahoo Finance and Wikipedia from the user's own connection. Providers receive ordinary connection information and requested symbols. No developer account, local service or broker login is inherited.

Runtime data is stored in the user's Nightdesk data folder, separate from source assets. The optional reader is configured by that user and loads local compatible safetensors. Video voice downloads are optional public assets verified against pinned SHA-256 hashes. The core forecasting model is generated locally as JSON.

## Before publishing any change

1. Review the actual staged file list with `git diff --cached --name-only`.
2. Run `python3 tools/privacy_check.py --history`; it checks the index and all reachable Git versions.
3. Inspect content that the scanner cannot interpret, including prose, URLs and research notes. Add any project-specific private terms with `--deny-text-file` pointing to a file outside the repository.
4. Commit with a GitHub noreply address if you do not want your personal email in commit metadata.

Ignore rules prevent accidental addition; they are not a security boundary. An ignored file can still be force-added. CI repeats the privacy gate. Generated runtime or media artifacts must never be included in source commits or releases.

No tool can certify absolute confidentiality. This policy combines strict inclusion rules, source review, staged-blob and history scanning, and isolated first-run tests. Report any privacy issue privately to the repository owner through GitHub; do not post the confidential content in a public issue.

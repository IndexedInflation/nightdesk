# Nightdesk contributor handoff

Practice money only. Never add real trading or broker credentials.
Keep this repository free of personal data, account state, logs, cached prices, model weights, machine paths, and secrets. Each user downloads data and trains their own model locally. Optional headline models must be configured by that user.
Do not change the original source installations.
Keep day-t features causal; fills use the next fresh price; live portfolio rules must match backtests. Do not tune on 2025 onward. Research results describe historical simulations and are not promises.
Tests must use isolated temporary account directories, never a user's existing account. Money-math changes need failing tests before implementation.

# Wiki Tooling Setup

This repo is the local control plane for the private NAS wiki. The Markdown
wiki on the NAS is canonical; everything under `state/`, `backups/`, and
`patch_bundles/` is local generated support state unless explicitly promoted.

## MacBook Setup

Prerequisites:

- macOS with Python 3.11 or newer.
- The NAS wiki mounted at `/Volumes/wiki`.
- Local repo checked out at `~/dev/wiki`.

Verify the local environment:

```bash
cd ~/dev/wiki
python3 --version
test -d /Volumes/wiki
git status --short
```

Optional isolated Python environment:

```bash
cd ~/dev/wiki
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -e .
```

The default toolchain has no third-party runtime dependencies. Run commands
from the repo root with `python3 -m wiki_tool ...`, or install the package in
editable mode and use the shorter `wiki ...` command from the active virtual
environment.

## Live Codex Cloud Preflight

The Rust cleanroom live-proof path is now fail-fast. Before any live Codex
cloud run, all of these must be true:

- `config/codex_cloud_env.json` exists and names the exact immutable
  `environment_id`
- `CODEX_CLOUD_ENV_ID` exactly matches that manifest `environment_id`
- the git worktree is clean
- at least one fetch remote matches an allowed normalized identity from the
  manifest
- `HEAD` is already contained in a remote-tracking ref for an allowed fetch
  remote

Tracked manifest shape:

```json
{
  "version": 1,
  "environment_id": "69eb85e9b3088191aa1fde9e14dc37d4",
  "environment_label": "KOGA-ryu/skynet",
  "allowed_fetch_remote_identities": [
    "github.com/koga-ryu/skynet"
  ]
}
```

The checked-in manifest now matches the current live Codex environment binding.
If the environment changes later, update `config/codex_cloud_env.json` and
`CODEX_CLOUD_ENV_ID` together before running the live proof again.

## Codex Cloud Probe

Use the probe binary when you want the same Codex worker and preflight path
without running the full cleanroom review flow. It prints a JSON report with
the cloud-run status, task id, artifact paths, and command-trace summaries,
and exits nonzero on failure.

```bash
cd ~/dev/skynet
CODEX_CLOUD_ENV_ID=69eb85e9b3088191aa1fde9e14dc37d4 cargo run --bin codex_cloud_probe -- --attempt-index 1
```

This probe still enforces the same preflight truth as the live proof:

- tracked `config/codex_cloud_env.json`
- exact `environment_id` match
- clean git worktree
- allowed fetch remote binding
- pushed `HEAD`

## Codex Cloud Canary

Use the canary when you want the smallest live proof of repository diffability.
It bypasses packet/schema logic and asks Codex Cloud to write exactly one file
in a visible tracked path, while still persisting the run, traces, and audit
events through `cleanroom.db`.

```bash
cd ~/dev/skynet
CODEX_CLOUD_ENV_ID=69eb85e9b3088191aa1fde9e14dc37d4 cargo run --bin codex_cloud_canary -- --db-path cleanroom.db --canary-id canary-001
```

Interpretation:

- success means the environment can produce at least one diff for this repo
- `cloud_no_diff` means the environment still cannot produce any diff for this
  repo/environment binding

## First Local Build

Build the derived catalog from the mounted NAS wiki:

```bash
cd ~/dev/wiki
python3 -m wiki_tool scan --wiki-root /Volumes/wiki --json
python3 -m wiki_tool audit --json
python3 -m wiki_tool health --wiki-root /Volumes/wiki --json
```

For normal build work, refresh the ignored local mirror once and scan that
copy. This avoids repeated NAS traversal while keeping the NAS as canonical:

```bash
cd ~/dev/wiki
tools/sync_wiki_mirror.sh
python3 -m wiki_tool scan --wiki-root state/wiki_mirror --json
python3 -m wiki_tool audit --json
```

Expected healthy baseline:

- audit status is `pass`
- actionable broken links are `0`
- `state/catalog.sqlite` exists after scan
- unit tests and harness spec validation pass in the health output

Useful read-only smoke checks:

```bash
wiki --help
python3 -m wiki_tool find "adapter boundary" --json
python3 -m wiki_tool explain "adapter boundary" --json
python3 -m wiki_tool harness answer "adapter boundary" --synthesis deterministic --json
python3 -m wiki_tool eval run --limit 3 --json
python3 -m wiki_tool source-shelves summary --json
python3 -m wiki_tool source-shelves bridge-bundle math --output patch_bundles/source_shelves_math_bridge_map.json --json
python3 -m wiki_tool source-shelves bridge-bundle computer --output patch_bundles/source_shelves_computer_project_bridge_map.json --json
python3 -m wiki_tool page-quality stub-fill-queue --limit 25 --json
python3 -m wiki_tool intake validate --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --json
python3 -m wiki_tool intake write --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --output-dir state/intake --json
python3 -m wiki_tool api request --request-json '{"jsonrpc":"2.0","id":1,"method":"symbol.search","params":{"query":"adapter boundary","limit":3}}' --json
```

After the editable install, `wiki find "adapter boundary" --json` is equivalent
to `python3 -m wiki_tool find "adapter boundary" --json`. The long form remains
the portable fallback when the package has not been installed into the active
environment.

## Generated Local State

Ignored local outputs:

- `state/wiki_mirror/`: local working copy of the NAS wiki, excluding heavy
  generated/data payloads configured in `config/wiki_mirror_excludes.txt`.
- `state/catalog.sqlite`: derived catalog rebuilt by `scan`.
- `state/harness.sqlite`: executable harness traces.
- `state/api_traces.jsonl`: JSON-RPC API request traces.
- `state/eval_reports/`: optional Markdown eval reports.
- `state/project_reports/`: optional local project reports.
- `state/source_shelf_reports/`: optional local math/computer source shelf
  reports for staging-library cleanup.
- `state/intake/`: optional local repo-demand intake queues and librarian
  packets.
- `backups/`: NAS edit backups from applied patch bundles.
- `patch_bundles/`: reviewable local or NAS edit plans.

These files are intentionally not committed. Rebuild them from the NAS wiki and
source-controlled tooling when needed.

## Optional OpenAI Harness Mode

Deterministic harness mode is the default and does not need network access or
an API key. OpenAI-backed synthesis is opt-in:

```bash
export OPENAI_API_KEY="..."
export WIKI_OPENAI_MODEL="gpt-5.4-mini"
python3 -m wiki_tool harness answer "adapter boundary" --synthesis openai --json
```

Do not use OpenAI-backed mode for routine verification unless the task
explicitly requires checking the LLM adapter.

## Local Claim-Plan Harness Mode

Local harness mode is available through `--synthesis local` and is designed to
improve contract integrity, not to magically make a small local model smart.

The local path is claim-plan-first:

- the model returns `refusal`, `refusal_reason`, and atomic `claims`
- each claim must reference retrieved `span_ids`
- the harness validates that plan before building the final answer
- final citations and quote text are rendered deterministically from retrieved
  spans rather than generated by the model

This is the important boundary: the system now guarantees deterministic
citation rendering from retrieved spans, but answer usefulness still depends on
retrieval quality, claim-plan quality, and refusal thresholds.

Useful local checks:

```bash
python3 -m wiki_tool harness answer "adapter boundary" --synthesis local --json
python3 -m wiki_tool harness show <run_id> --json
python3 -m wiki_tool eval run --split holdout --synthesis local --json
python3 -m wiki_tool eval export-training --output state/training_exports/training_examples.jsonl --json
```

Interpret local failures conservatively:

- refusal is preferable to fabricated quotes
- a refusal-heavy run usually means thin retrieval, strict policy thresholds,
  or a conservative local model
- claim text is still model-authored, so unsupported connective tissue remains
  possible if claims are not truly atomic
- the repair loop allows one controlled retry for invalid claim plans or bad
  span references; first-pass and repaired validity should be treated as
  separate metrics

Training export is a hard-wall boundary. Do not include dev or holdout eval
queries in training data.

## Safe NAS Edit Workflow

Read-only commands can run directly against `state/catalog.sqlite` after a
scan. Any NAS write should go through the patch-bundle flow:

Schema details and supported target types are documented in
[PATCH_BUNDLE_SCHEMA.md](PATCH_BUNDLE_SCHEMA.md).

Local source-shelf cleanup bundles should be applied to `state/wiki_mirror`
first; use `/Volumes/wiki` only during an explicit promotion pass.

Repo-demand intake is local-first. Use `intake validate` and `intake write` to
stage reviewed repo findings under ignored `state/intake/`; use `intake bundle`
only when a reviewable patch bundle is needed.

```bash
python3 -m wiki_tool source-shelves cleanup-bundle computer --output patch_bundles/source_shelves_computer_cleanup.json --json
python3 -m wiki_tool source-shelves bridge-bundle math --output patch_bundles/source_shelves_math_bridge_map.json --json
python3 -m wiki_tool source-shelves bridge-bundle computer --output patch_bundles/source_shelves_computer_project_bridge_map.json --json
python3 -m wiki_tool patch-bundle validate patch_bundles/<bundle>.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle report patch_bundles/<bundle>.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle apply patch_bundles/<bundle>.json --wiki-root /Volumes/wiki --dry-run --json
python3 -m wiki_tool patch-bundle apply patch_bundles/<bundle>.json --wiki-root /Volumes/wiki --json
```

Real applies check that the bundle/catalog scan root matches `--wiki-root`.
After working from `state/wiki_mirror`, rebuild the catalog from `/Volumes/wiki`
before any NAS write.

After applying a bundle:

```bash
python3 -m wiki_tool scan --wiki-root /Volumes/wiki --json
python3 -m wiki_tool audit --json
python3 -m unittest discover -s tests
```

Rollback an applied bundle by manifest if verification exposes a problem:

```bash
python3 -m wiki_tool patch-bundle report backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --dry-run --json
python3 -m wiki_tool patch-bundle rollback backups/<bundle>/manifest.json --wiki-root /Volumes/wiki --json
```

## Future PC Setup

The Windows PC is intentionally tapped off for now because the machine may move
to Linux. Keep Windows/Linux dev-root configuration out of the shared repo until
the OS decision is final.

Current local dev reference defaults:

- Mac dev root: `/Users/kogaryu/dev`
- Windows dev root: unconfigured by default
- Optional local config path: `state/devrefs_config.json`

When the future PC environment is final, configure the local dev root there and
verify:

```bash
python3 -m wiki_tool open <wiki-path> --platform windows --json
python3 -m wiki_tool open dev://<repo>/<path> --platform windows --json
```

If the PC becomes Linux, add Linux path support as a separate implementation
task instead of overloading the Windows defaults.

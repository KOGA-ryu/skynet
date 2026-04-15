# Repo Demand Intake Manifest

Repo-demand intake uses a reviewed JSON manifest. The manifest captures findings
from a repo before they are promoted into durable wiki pages.

## Required Shape

```json
{
  "intake_id": "demo_repo_demand",
  "title": "Demo Repo Demand",
  "topic": "library_operations",
  "source_type": "repo_demand",
  "repo": {
    "name": "demo-repo",
    "url": "https://example.com/demo-repo",
    "branch": "main",
    "commit": "abc1234"
  },
  "findings": [
    {
      "id": "demo.adapter_boundary",
      "title": "Adapter boundary before core logic",
      "summary": "Keep external adapters outside the core service boundary.",
      "confidence": "docs-confirmed",
      "status": "routed",
      "tags": ["adapter_boundary"],
      "selected_targets": ["concepts/architecture.md"],
      "evidence": [
        {"path": "README.md", "line": 3, "label": "adapter notes"}
      ]
    }
  ]
}
```

## Rules

- `intake_id`, `title`, `topic`, `source_type`, `repo`, and `findings` are
  required.
- `repo.name` is required; `url`, `branch`, and `commit` are optional.
- Every finding needs `id`, `title`, `summary`, `confidence`, and non-empty
  `evidence`.
- Status defaults to `captured`.
- Allowed statuses: `captured`, `staged`, `routed`, `promoted`, `deferred`,
  `rejected`.
- Allowed confidence values: `code-confirmed`, `docs-confirmed`,
  `docs-and-code-confirmed`, `operator-confirmed`, `repo-confirmed`,
  `user-confirmed`, `inference`, `unreviewed`.
- Evidence paths are repo-relative when `--repo-root` is provided. Missing
  evidence paths produce warnings; paths escaping the repo are rejected.

## Commands

```bash
python3 -m wiki_tool intake validate --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --json
python3 -m wiki_tool intake write --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --output-dir state/intake --json
python3 -m wiki_tool intake bundle --input tests/fixtures/intake/demo_manifest.json --repo-root tests/fixtures/intake/repo --wiki-root state/wiki_mirror --output patch_bundles/intake_demo_repo_demand.json --json
```

`intake write` creates ignored local Markdown under `state/intake/`.
`intake bundle` creates a reviewable patch bundle but does not apply it.

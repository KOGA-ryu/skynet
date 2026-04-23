# NAS Study Migration Checklist

This checklist is for moving the study stack onto the NAS without losing the
canonical corpus or any local study continuity you care about.

Current live baseline on this machine:

- built study corpus: `60` books under `state/study_materials/math`
- study QA: `completion_status=pass`
- `vox`: active local resume state exists
- `discoflash`: little or no active local session state exists

## Must Preserve

### Canonical study corpus

If the NAS is meant to hold the actual study-library payload, preserve these:

- [state/study_materials/math](state/study_materials/math)
- [state/study_quality/math/summary.json](state/study_quality/math/summary.json)

`state/study_materials/math` is the real source of truth for:

- per-book `manifest.json`
- `reader_stream.jsonl`
- `reader_plain.txt`
- `definition_cards.jsonl`
- chapter structure and app-facing study metadata

If you preserve only one study-data path, preserve `state/study_materials/math`.

### App-local continuity state

Copy these only if you want to preserve local resumes, completions, review
cadence, and study history.

`vox`

- `/Users/kogaryu/dev/vox/.session_memory/reading_progress.json`
- `/Users/kogaryu/dev/vox/.session_memory/study_events.jsonl`
- `/Users/kogaryu/dev/vox/.session_memory/study_review.json`
- `/Users/kogaryu/dev/vox/.session_memory/study_completion.json` if present

`discoflash`

- `/Users/kogaryu/dev/discoflash/.session_memory/definition_matching_progress.json` if present
- `/Users/kogaryu/dev/discoflash/.session_memory/study_events.jsonl` if present
- `/Users/kogaryu/dev/discoflash/.session_memory/study_review.json`
- `/Users/kogaryu/dev/discoflash/.session_memory/study_completion.json` if present

These files are the only app-local source of truth for:

- active resume state
- durable chapter completion
- due-review cadence
- append-only study history

## Regenerable

These are safe to rebuild later if the canonical corpus is preserved:

- [state/wiki_mirror/projects/math_library](state/wiki_mirror/projects/math_library)
- [state/wiki_mirror/projects/study_dashboard](state/wiki_mirror/projects/study_dashboard)
- [state/wiki_mirror/index.md](state/wiki_mirror/index.md)
- [state/study_quality/math/README.md](state/study_quality/math/README.md)
- [state/study_quality/math/final_review_packet.md](state/study_quality/math/final_review_packet.md)

These are derived outputs:

- generated Math Library pages
- generated Study Dashboard pages
- machine-readable dashboard index
- human-readable QA summaries and review packets

If you want immediate NAS browsing without a rebuild, copy them. If you want the
minimal canonical payload, preserve only `state/study_materials/math` and
rebuild these later.

## Safe To Ignore

You do not need these to preserve the study system itself:

- `__pycache__/`
- `.venv/`
- `.benchchain/`
- `.bluebench/`
- `.workstate/`
- benchmark reports
- test temp outputs

Do not treat the source repos as required NAS study payload unless you explicitly
want full repo backup:

- `/Users/kogaryu/dev/wiki`
- `/Users/kogaryu/dev/vox`
- `/Users/kogaryu/dev/discoflash`

If your goal is only “books plus study state on NAS,” the repos are separate
from the content payload.

## Recommended Copy Order

1. Copy [state/study_materials/math](state/study_materials/math).
2. Copy [state/study_quality/math/summary.json](state/study_quality/math/summary.json).
3. Copy whichever `.session_memory` files you want to preserve from `vox` and
   `discoflash`.
4. Optionally copy
   [state/wiki_mirror/projects/study_dashboard](state/wiki_mirror/projects/study_dashboard)
   and
   [state/wiki_mirror/projects/math_library](state/wiki_mirror/projects/math_library)
   for immediate read access on the NAS.

## Post-Move Verification

After the move, verify:

- the NAS copy still has `60` book directories under `state/study_materials/math`
- `state/study_quality/math/summary.json` still reports:
  - `completion_status=pass`
  - `remaining_severe_count=0`
  - `remaining_warning_count=0`
- if app state was copied:
  - `vox` still sees the expected last selection and resume point
  - `discoflash` still sees any copied review/completion/session files
- if generated pages were copied:
  - `projects/study_dashboard/README.md` opens cleanly
  - `projects/study_dashboard/state/navigation_index.json` is present and readable

## Current Local State Snapshot

At the time this checklist was written:

- dashboard books: `60`
- `Continue Studying`
  - `vox_resume = 1`
  - `discoflash_resume = 0`
  - `fresh_recommendations = 59`
- `Study Journal`
  - `books_with_active_resume = 1`
  - `books_with_completion = 0`
  - `fully_completed_books = 0`
  - `total_completed_chapters = 0`
- `Recent Activity = 0`
- `Recently Completed = 0`
- `Next Up = 0`
- `Review Queue = 0`

This means the infrastructure is in place, but very little live study history or
completion state has accumulated yet.

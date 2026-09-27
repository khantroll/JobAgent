# JobAgent 2.0 Alpha.3 — Review → Application Package

Alpha.3 closes the gap between a good candidate/job match and a human-reviewed application package.

## Workflow

1. Discovery, ranking, and commute evaluation remain Alpha.2 behavior.
2. A person reviews a candidate-specific match.
3. After the operator explicitly sets a match to `reviewed`, **Build package** generates a tailored resume and cover-letter draft.
4. Drafts are stored under `output/resumes/{candidate_id}/` and `output/cover_letters/{candidate_id}/`.
5. The operator reviews and edits both drafts in the local UI and saves them.
6. The match stores only its candidate-specific document paths.

## Safety boundary

Alpha.3 does **not** submit applications, drive a browser, send mail, schedule submissions, or change `AUTO_APPLY_ENABLED`. Marking a match applied remains manual bookkeeping.

Generated text must be reviewed by a person. The resume prompt explicitly requires factual accuracy and prohibits invented experience.

## Isolation

Application package files are namespaced by candidate and job. Package reads/writes reject paths outside that candidate's two output directories. A candidate's package cannot reuse another candidate's match state or document path.

## Configuration

Generation uses the existing provider abstraction and the selected candidate's runtime configuration. A live Anthropic/Mistral provider requires its configured key. The existing mock provider remains suitable for deterministic tests.

## Tests

`tests/test_application_package.py` verifies generation/edit persistence and the approval boundary. Existing isolation tests continue to protect shared-job/per-candidate state.
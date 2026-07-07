---
name: Agentic Update Self-Heal

on:
  workflow_run:
    workflows:
      - Update
      - "Update: Certify"
      - CI
    types:
      - completed
    branches:
      - main
      - "agentic/update-self-heal/**"
  workflow_dispatch:
    inputs:
      run-id:
        description: Failed workflow run id to investigate.
        required: false
        type: string
      campaign-key:
        description: Optional campaign key. Defaults to update branch plus first failing run id.
        required: false
        type: string

if: >-
  github.event_name == 'workflow_dispatch' ||
  (
    github.event.workflow_run.conclusion == 'failure' &&
    (
      github.event.workflow_run.name == 'Update' ||
      github.event.workflow_run.name == 'Update: Certify' ||
      (
        github.event.workflow_run.name == 'CI' &&
        startsWith(github.event.workflow_run.head_branch, 'agentic/update-self-heal/')
      )
    )
  )

permissions:
  actions: read
  checks: read
  contents: read
  issues: read
  pull-requests: read

engine:
  id: copilot
  model: gpt-5.5

checkout:
  fetch-depth: 0

tools:
  edit:
  bash:
    - "gh *"
    - "git *"
    - "jq *"
    - "nix *"
    - "python3 *"
    - "rg *"
    - "sed *"
    - "uv *"

network:
  allowed:
    - defaults
    - github
    - python
    - node
    - rust
    - go
    - containers
    - linux-distros
    - "cache.nixos.org"
    - "*.cachix.org"

steps:
  - name: Fail closed when required secrets are absent
    env:
      COPILOT_GITHUB_TOKEN: ${{ secrets.COPILOT_GITHUB_TOKEN }}
      UPDATE_SELF_HEAL_GITHUB_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
    run: |
      set -euo pipefail
      test -n "${COPILOT_GITHUB_TOKEN}" || {
        echo "COPILOT_GITHUB_TOKEN is required for the configured Copilot engine." >&2
        exit 1
      }
      test -n "${UPDATE_SELF_HEAL_GITHUB_TOKEN}" || {
        echo "UPDATE_SELF_HEAL_GITHUB_TOKEN is required for safe write operations." >&2
        exit 1
      }
  - name: Fail closed when campaign budget is exhausted
    env:
      GH_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
      LEDGER_TITLE: Agentic update self-healing ledger
      MANUAL_CAMPAIGN_KEY: ${{ inputs['campaign-key'] || '' }}
      MANUAL_RUN_ID: ${{ inputs['run-id'] || '' }}
      REPAIR_BRANCH_PREFIX: agentic/update-self-heal/
      WORKFLOW_RUN_HEAD_BRANCH: ${{ github.event.workflow_run.head_branch || '' }}
      WORKFLOW_RUN_ID: ${{ github.event.workflow_run.id || '' }}
    run: |
      set -euo pipefail
      campaign_key="${MANUAL_CAMPAIGN_KEY}"
      if [ -z "${campaign_key}" ] && [ -n "${WORKFLOW_RUN_HEAD_BRANCH}" ]; then
        repair_campaign="${WORKFLOW_RUN_HEAD_BRANCH#"${REPAIR_BRANCH_PREFIX}"}"
        if [ "${repair_campaign}" != "${WORKFLOW_RUN_HEAD_BRANCH}" ]; then
          campaign_key="${repair_campaign}"
        fi
      fi
      if [ -z "${campaign_key}" ] && [ -n "${MANUAL_RUN_ID}" ]; then
        campaign_key="update_flake_lock_action-${MANUAL_RUN_ID}"
      fi
      if [ -z "${campaign_key}" ] && [ -n "${WORKFLOW_RUN_ID}" ]; then
        campaign_key="update_flake_lock_action-${WORKFLOW_RUN_ID}"
      fi
      if [ -z "${campaign_key}" ]; then
        echo "No campaign key is available; skipping budget preflight."
        exit 0
      fi

      issue_json="$(
        gh issue list --state all --limit 100 --json number,title \
          --jq ".[] | select(.title == env.LEDGER_TITLE) | @json" | head -n 1
      )"
      if [ -z "${issue_json}" ]; then
        echo "No self-healing ledger exists; campaign has full budget."
        exit 0
      fi
      issue_number="$(jq -r '.number' <<<"${issue_json}")"
      comments_json="$(mktemp)"
      gh issue view "${issue_number}" --json comments >"${comments_json}"
      python3 lib/update/ci/self_heal.py remaining-cycles \
        --comments-json "${comments_json}" \
        --campaign-key "${campaign_key}" >/dev/null
  - name: Collect failed run evidence for classifier
    env:
      GH_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
      MANUAL_RUN_ID: ${{ inputs['run-id'] || '' }}
      WORKFLOW_RUN_ID: ${{ github.event.workflow_run.id || '' }}
    run: |
      set -euo pipefail
      evidence_dir="/tmp/gh-aw/agent/evidence"
      mkdir -p "${evidence_dir}/logs"

      target_run_id="${MANUAL_RUN_ID:-${WORKFLOW_RUN_ID}}"
      test -n "${target_run_id}" || {
        echo "No failed workflow run id is available for classifier evidence." >&2
        exit 1
      }

      printf '%s\n' "${target_run_id}" >"${evidence_dir}/target-run-id.txt"
      cp "${GITHUB_EVENT_PATH}" "${evidence_dir}/event.json"
      gh run view "${target_run_id}" \
        --json databaseId,name,workflowName,status,conclusion,event,headBranch,headSha,url,createdAt,updatedAt,jobs \
        >"${evidence_dir}/run.json"
      jq '[.jobs[] | select(.conclusion == "failure") | {databaseId,name,conclusion,url}]' \
        "${evidence_dir}/run.json" >"${evidence_dir}/failed-jobs.json"
      jq -e 'length > 0' "${evidence_dir}/failed-jobs.json" >/dev/null

      jq -r '.[].databaseId' "${evidence_dir}/failed-jobs.json" |
        while IFS= read -r job_id; do
          gh api "/repos/${GITHUB_REPOSITORY}/actions/jobs/${job_id}/logs" \
            >"${evidence_dir}/logs/job-${job_id}.log"
        done

      {
        printf 'Target run: %s\n' "${target_run_id}"
        jq -r '"Workflow: \(.workflowName)\nBranch: \(.headBranch)\nSHA: \(.headSha)\nURL: \(.url)"' \
          "${evidence_dir}/run.json"
        printf 'Failed jobs:\n'
        jq -r '.[] | "- \(.name) (\(.databaseId)): \(.url)"' \
          "${evidence_dir}/failed-jobs.json"
      } >"${evidence_dir}/summary.txt"

safe-outputs:
  github-token: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
  create-pull-request:
    title-prefix: "[agentic update repair] "
    labels:
      - agentic-update-repair
    draft: false
    max: 1
    base-branch: main
    allowed-base-branches:
      - main
    preserve-branch-name: true
    fallback-as-issue: false
    auto-close-issue: false
    protected-files: allowed
    allowed-files:
      - "packages/**"
      - "overlays/**"
      - "lib/tests/**"
      - "tests/**"
      - "docs/**"
      - "misc/**"
    github-token-for-extra-empty-commit: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
  jobs:
    retry-failed-jobs:
      description: Rerun failed jobs from a transient update failure after budget checks pass.
      runs-on: ubuntu-24.04
      permissions:
        actions: write
        contents: read
        issues: write
      inputs:
        campaign_key:
          description: Campaign key, normally update branch plus first failing run id.
          required: true
          type: string
        run_id:
          description: GitHub Actions run id whose failed jobs should be rerun.
          required: true
          type: string
        job_ids:
          description: JSON array of failed job databaseId values to rerun.
          required: true
          type: string
        reason:
          description: Short retry reason.
          required: true
          type: string
        evidence:
          description: Evidence supporting the transient classification.
          required: true
          type: string
      steps:
        - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
          with:
            ref: main
            persist-credentials: false
        - name: Record campaign and rerun failed jobs
          env:
            GH_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
            LEDGER_TITLE: Agentic update self-healing ledger
          run: |
            set -euo pipefail
            test -n "${GH_TOKEN}" || {
              echo "UPDATE_SELF_HEAL_GITHUB_TOKEN is required." >&2
              exit 1
            }
            item="$(
              jq -c '.items[] | select(.type == "retry_failed_jobs")' \
                "${GH_AW_AGENT_OUTPUT}" | tail -n 1
            )"
            test -n "${item}" || {
              echo "retry_failed_jobs output was not present." >&2
              exit 1
            }
            campaign_key="$(jq -r '.campaign_key' <<<"${item}")"
            run_id="$(jq -r '.run_id' <<<"${item}")"
            job_ids="$(jq -r '.job_ids' <<<"${item}")"
            reason="$(jq -r '.reason' <<<"${item}")"
            evidence="$(jq -r '.evidence' <<<"${item}")"

            issue_json="$(
              gh issue list --state all --limit 100 --json number,title,state \
                --jq ".[] | select(.title == env.LEDGER_TITLE) | @json" | head -n 1
            )"
            if [ -z "${issue_json}" ]; then
              issue_url="$(
                gh issue create \
                  --title "${LEDGER_TITLE}" \
                  --body "Evergreen ledger for unattended update self-healing campaigns."
              )"
              issue_number="${issue_url##*/}"
            else
              issue_number="$(jq -r '.number' <<<"${issue_json}")"
              issue_state="$(jq -r '.state' <<<"${issue_json}")"
              if [ "${issue_state}" = "CLOSED" ]; then
                gh issue reopen "${issue_number}"
              fi
            fi

            comments_json="$(mktemp)"
            gh issue view "${issue_number}" --json comments >"${comments_json}"
            set +e
            remaining="$(
              python3 lib/update/ci/self_heal.py remaining-cycles \
                --comments-json "${comments_json}" \
                --campaign-key "${campaign_key}"
            )"
            remaining_status="$?"
            set -e
            if [ "${remaining_status}" -ne 0 ]; then
              comment_body="$(mktemp)"
              python3 lib/update/ci/self_heal.py render-ledger-comment \
                --campaign-key "${campaign_key}" \
                --attempt-kind stop \
                --run-id "${run_id}" \
                --status exhausted \
                --reason "campaign exhausted automatic retry cycles" \
                --evidence "retry request: ${reason}" \
                --evidence "failed jobs: ${job_ids}" >"${comment_body}"
              gh issue comment "${issue_number}" --body-file "${comment_body}"
              echo "Campaign ${campaign_key} has no remaining cycles." >&2
              exit 1
            fi

            comment_body="$(mktemp)"
            python3 lib/update/ci/self_heal.py render-ledger-comment \
              --campaign-key "${campaign_key}" \
              --attempt-kind retry \
              --run-id "${run_id}" \
              --status rerun \
              --reason "${reason}" \
              --evidence "remaining cycles before this attempt: ${remaining}" \
              --evidence "${evidence}" >"${comment_body}"
            gh issue comment "${issue_number}" --body-file "${comment_body}"

            jq -e 'type == "array" and length > 0' <<<"${job_ids}" >/dev/null
            jq -r '.[]' <<<"${job_ids}" | while IFS= read -r job_id; do
              gh run rerun "${run_id}" --job "${job_id}"
            done
    record-stop:
      description: Record a stopped campaign with evidence in the evergreen ledger.
      runs-on: ubuntu-24.04
      permissions:
        contents: read
        issues: write
      inputs:
        campaign_key:
          description: Campaign key, normally update branch plus first failing run id.
          required: true
          type: string
        run_id:
          description: GitHub Actions run id that caused the stop decision.
          required: true
          type: string
        reason:
          description: Refusal or stop reason.
          required: true
          type: string
        evidence:
          description: Evidence supporting the stop decision.
          required: true
          type: string
      steps:
        - uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2
          with:
            ref: main
            persist-credentials: false
        - name: Record stop in evergreen issue
          env:
            GH_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
            LEDGER_TITLE: Agentic update self-healing ledger
          run: |
            set -euo pipefail
            test -n "${GH_TOKEN}" || {
              echo "UPDATE_SELF_HEAL_GITHUB_TOKEN is required." >&2
              exit 1
            }
            item="$(
              jq -c '.items[] | select(.type == "record_stop")' \
                "${GH_AW_AGENT_OUTPUT}" | tail -n 1
            )"
            test -n "${item}" || {
              echo "record_stop output was not present." >&2
              exit 1
            }
            campaign_key="$(jq -r '.campaign_key' <<<"${item}")"
            run_id="$(jq -r '.run_id' <<<"${item}")"
            reason="$(jq -r '.reason' <<<"${item}")"
            evidence="$(jq -r '.evidence' <<<"${item}")"

            issue_json="$(
              gh issue list --state all --limit 100 --json number,title,state \
                --jq ".[] | select(.title == env.LEDGER_TITLE) | @json" | head -n 1
            )"
            if [ -z "${issue_json}" ]; then
              issue_url="$(
                gh issue create \
                  --title "${LEDGER_TITLE}" \
                  --body "Evergreen ledger for unattended update self-healing campaigns."
              )"
              issue_number="${issue_url##*/}"
            else
              issue_number="$(jq -r '.number' <<<"${issue_json}")"
              issue_state="$(jq -r '.state' <<<"${issue_json}")"
              if [ "${issue_state}" = "CLOSED" ]; then
                gh issue reopen "${issue_number}"
              fi
            fi

            comment_body="$(mktemp)"
            python3 lib/update/ci/self_heal.py render-ledger-comment \
              --campaign-key "${campaign_key}" \
              --attempt-kind stop \
              --run-id "${run_id}" \
              --status stopped \
              --reason "${reason}" \
              --evidence "${evidence}" >"${comment_body}"
            gh issue comment "${issue_number}" --body-file "${comment_body}"
---

# Agentic Update Self-Heal

You are the unattended repair agent for the `Update` and
`Update: Certify` workflows.

## Invariants

- Use the configured Copilot model. If the configured model or token fails, stop; do not fall back.
- The campaign key is `update_flake_lock_action-<first failing run id>` unless manual
  dispatch supplied another key.
- A campaign has at most 3 automatic cycles. Each retry or repair attempt consumes 1 cycle.
- Never react to ordinary `CI` failures on the generated `update_flake_lock_action` branch.
  Only investigate `CI` failures when the head branch starts with
  `agentic/update-self-heal/`.
- Do not edit workflow logic, updater core, host/home/module config, secrets, auth, or
  non-derivation-specific Nix during auto-fix.

## Classifier Stage

Inspect the failed run, failed jobs, and logs. The workflow has already
materialized classifier evidence under `/tmp/gh-aw/agent/evidence/`:

- `target-run-id.txt`: the failed `Update`, `Update: Certify`, or repair `CI` run.
- `event.json`: the triggering workflow event.
- `run.json`: run metadata and job metadata from GitHub Actions.
- `failed-jobs.json`: the failed job database IDs and URLs.
- `logs/job-<databaseId>.log`: failed job logs.
- `summary.txt`: a short index of the same evidence.

Classify the run named in `target-run-id.txt`; do not classify the current
`Agentic Update Self-Heal` workflow run. There is no deterministic
`classify` helper; inspect the evidence yourself, write `classifier.json`,
and use the parser command below only to validate its shape. Output exactly one
decision:

- `retry`: the evidence points to transient infrastructure such as network flakes,
  GitHub API timeouts, runner loss, cache service failures, or other retryable failures.
- `auto_fix`: the failure is limited to upstream source or derivation drift, stale
  generated package artifacts, or a derivation-specific updater failure.
- `stop`: the failure touches updater core, workflow logic, non-derivation-specific Nix,
  host/home/module config, auth/secrets, ambiguous evidence, or exhausted budget.

Known classifications:

- Choose `retry` for transient fetch and service failures, including DNS failures,
  HTTP 5xx responses from source hosts, GitHub API timeouts, runner loss, Cachix
  service failures, `nix-prefetch-git` transport errors, and npm registry network
  failures where the same inputs should plausibly pass without edits.
- Choose `auto_fix` for derivation-specific drift such as fixed-output hash
  mismatches, `sources.json` updates, `Cargo.nix` / `crate-hashes.json` drift,
  `uv.lock` drift, `pnpm` lockfile config mismatches inside one package,
  missing vendored runtime dependencies, package-specific path drift such as a
  moved bundled binary, and package updater failures that only affect
  `packages/**` or `overlays/**`.
- Choose `stop` when a fix would require workflow restructuring, updater core
  changes, cache policy changes, host closure changes, secrets, credentials,
  branch protection, or edits outside the allowed lanes.

Use this JSON shape internally before taking action:

```json
{
  "decision": "retry | auto_fix | stop",
  "campaign_key": "update_flake_lock_action-123456789",
  "run_id": "123456789",
  "evidence": ["specific log line or artifact finding"],
  "reason": "short rationale",
  "failed_job_ids": ["databaseId values for retry decisions"],
  "affected_paths": ["paths for auto_fix decisions"]
}
```

Write that JSON to a file and run the deterministic parser before any action:

```sh
python3 lib/update/ci/self_heal.py parse-classifier classifier.json
```

## Retry Action

For `retry`, call the `retry_failed_jobs` safe-output tool. Pass failed job
`databaseId` values from `failed-jobs.json`.

Pass `job_ids` as a JSON array string. The deterministic safe-output job records the
ledger entry, checks the campaign budget, and reruns those failed jobs.

## Auto-Fix Action

For `auto_fix`, first confirm the campaign still has remaining automatic cycles in the
ledger; if it does not, choose `stop` instead of editing. Then make only the targeted
package or overlay lane changes. Allowed edit lanes are `packages/**`, `overlays/**`,
focused tests, focused docs, and `misc/**`. Run the narrow reproduction and focused
validation before opening a PR.

Before creating a PR, validate and render the same classifier JSON as a required
machine-readable PR marker:

```sh
python3 lib/update/ci/self_heal.py render-classifier-marker \
  --require-auto-fix classifier.json
```

Create a ready PR with the built-in `create_pull_request` safe-output tool:

- Target branch: `main`
- Head branch: `agentic/update-self-heal/<campaign-key>`
- Label: `agentic-update-repair`
- Title prefix: `[agentic update repair] `
- Body must include the classifier JSON, the exact
  `<!-- nixcfg-self-heal-classifier:{...} -->` marker from the command above,
  reproduction, validation, and cycle count.

The companion workflow owns auto-merge and update redispatch. Do not merge directly.

## Stop Action

For `stop`, call the `record_stop` safe-output tool with the campaign key, run id,
reason, and concise evidence. The ledger is the handoff surface.

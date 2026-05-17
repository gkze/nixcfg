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
  model: gpt-5

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
    - "cache.garnix.io"

steps:
  - name: Fail closed when required secrets are absent
    env:
      COPILOT_GITHUB_TOKEN: ${{ secrets.COPILOT_GITHUB_TOKEN }}
      UPDATE_SELF_HEAL_GITHUB_TOKEN: ${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}
    run: |
      set -euo pipefail
      test -n "${COPILOT_GITHUB_TOKEN}" || {
        echo "COPILOT_GITHUB_TOKEN is required for the Copilot GPT-5 engine." >&2
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
      python3 -m lib.update.ci.self_heal remaining-cycles \
        --comments-json "${comments_json}" \
        --campaign-key "${campaign_key}" >/dev/null

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
        - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
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
              python3 -m lib.update.ci.self_heal remaining-cycles \
                --comments-json "${comments_json}" \
                --campaign-key "${campaign_key}"
            )"
            remaining_status="$?"
            set -e
            if [ "${remaining_status}" -ne 0 ]; then
              comment_body="$(mktemp)"
              python3 -m lib.update.ci.self_heal render-ledger-comment \
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
            python3 -m lib.update.ci.self_heal render-ledger-comment \
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
        - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
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
            python3 -m lib.update.ci.self_heal render-ledger-comment \
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

- Use Copilot GPT-5. If the configured model or token fails, stop; do not fall back.
- The campaign key is `update_flake_lock_action-<first failing run id>` unless manual
  dispatch supplied another key.
- A campaign has at most 3 automatic cycles. Each retry or repair attempt consumes 1 cycle.
- Never react to ordinary `CI` failures on the generated `update_flake_lock_action` branch.
  Only investigate `CI` failures when the head branch starts with
  `agentic/update-self-heal/`.
- Do not edit workflow logic, updater core, host/home/module config, secrets, auth, or
  non-derivation-specific Nix during auto-fix.

## Classifier Stage

Inspect the failed run, failed jobs, and logs. Output exactly one decision:

- `retry`: the evidence points to transient infrastructure such as network flakes,
  GitHub API timeouts, runner loss, cache service failures, or other retryable failures.
- `auto_fix`: the failure is limited to upstream source or derivation drift, stale
  generated package artifacts, or a derivation-specific updater failure.
- `stop`: the failure touches updater core, workflow logic, non-derivation-specific Nix,
  host/home/module config, auth/secrets, ambiguous evidence, or exhausted budget.

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
python3 -m lib.update.ci.self_heal parse-classifier classifier.json
```

## Retry Action

For `retry`, call the `retry_failed_jobs` safe-output tool. Pass failed job
`databaseId` values from:

```sh
gh run view <run-id> --json jobs --jq '.jobs[] | select(.conclusion == "failure") | .databaseId'
```

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
python3 -m lib.update.ci.self_heal render-classifier-marker \
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

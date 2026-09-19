# Holdout evaluation set

Twenty frozen cases used to accept the investigator, per `docs/VERIFICATION.md` §3.
Four each of door exposure, refrigeration problem, sensor disagreement, ambiguous or
missing evidence, and normal control.

## Run it

```bash
make eval-offline
```

That runs every case through the real report pipeline using the **rule baseline** as
the proposer. It makes zero model calls and needs no AWS credentials.

Against the live model, which does spend money:

```bash
python3 scripts/run_eval.py --proposer bedrock --region "$AWS_REGION" --model-id "$BEDROCK_MODEL_ID"
```

Both write `outputs/<case_id>.json` and a `metrics.json` under `eval/reports/<timestamp>/`.
Scores are computed by reading those output files back, so no number in a report was
typed by hand.

## Freeze discipline

These cases are the **holdout**. Do not tune prompts against them. Tune on a separate
development set. If they are ever used for tuning, replace them and disclose the change
in the PR.

`eval/tests/test_holdout.py` pins a SHA-256 over all twenty snapshots. Changing a case
fails that test on purpose. Recompute the digest and explain in the PR why the holdout
moved.

## Label containment

Expected answers live in this package and nowhere else. Two things keep them out of a
deployed runtime:

- `infra/template.yaml` uses `CodeUri: ../backend/` for both Lambdas, so a repo-root
  `eval/` is structurally outside each bundle. A test asserts this.
- A per-case test asserts no case id, expected outcome, expected hypothesis or note
  appears anywhere in the snapshot the investigator receives.

The baseline and the investigator read the same tools against the same `ToolContext`.
Neither is handed a family or an expected answer.

## Reading the results

`clear_category_agreement` is scored over the twelve door, refrigeration and sensor
cases. `ambiguous_unresolved` requires all four ambiguous cases to decline to pick a
cause. `normal_correct` requires all four normal cases to return `no_excursion`, and
`normal_model_invocations` must be zero — a control that reaches the model is a defect
regardless of what it answers.

`total_proposer_invocations` counts how often a proposer ran; `total_model_invocations`
counts only real model traffic and stays at zero in baseline mode. They are reported
separately so a free run is never presented as a paid one.

## A result worth stating plainly

The rule baseline currently scores 12/12, 4/4 and 4/4. There is no categorisation
headroom above it. `docs/VERIFICATION.md` anticipates this: if the model matches the
baseline rather than beating it, report that honestly and assess explanation quality
separately. Do not present tool use as evidence of autonomous root-cause reasoning.

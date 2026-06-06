# DataAccum Handoff

This document summarizes the current state of the project, what has been built during this session, how the main pieces fit together, and what the present results mean.

## Current Goal

The project is building compile-grounded AIE debugging datasets for training and evaluating models on AMD/Xilinx Versal AIE and ADF code repair. The newest run is the v8 dataset, which was launched after hydrating missing project-local and repo-local files across the golden corpus and increasing the variant budget to 40 per project.

## What We Have Done

We moved the workflow from simple corpus cleanup into a full dataset-generation pipeline with three major improvements.

First, we added baseline retry logic to the dataset builder so projects whose correct version fails only because of missing headers can be repaired in place with temporary stub headers and retried instead of being dropped immediately.

Second, we created a dedicated hydrator that scans quoted includes in the golden projects, resolves them against the upstream repo described in the manifest, and writes the missing files back into the correct project-relative locations. That started as a project-local include filler and then expanded to a repo-wide resolver so it could recover files outside the immediate source root when the repo contains shared headers or support files in other directories.

Third, we reran the full corpus build as v8 with 40 variants per project so we could see how much extra data the larger mutation budget would actually survive through validation.

## The Important Files

The active scripts that control the workflow are:

- [scripts/build_v7_bug_dataset.py](scripts/build_v7_bug_dataset.py)
- [scripts/run_v7_parallel.py](scripts/run_v7_parallel.py)
- [scripts/hydrate_golden_missing_files.py](scripts/hydrate_golden_missing_files.py)
- [bug famalies/generate_bug_families_bedrock.py](bug%20famalies/generate_bug_families_bedrock.py)

The dataset outputs now live under:

- [data/processed/v8](data/processed/v8)

The hydration run log was written to:

- [archive_20260425/logs/hydrate_all_600_repo_wide.log](archive_20260425/logs/hydrate_all_600_repo_wide.log)

The v8 build log was written to:

- [archive_20260425/logs/run_v8_40variants.log](archive_20260425/logs/run_v8_40variants.log)

## How the Pipeline Works

### 1. Corpus loading

The dataset builder walks the golden corpus and reads each project folder into a normalized in-memory representation. It keeps the project files as relative paths and uses those files to infer whether the project is AIE or AIE-ML.

### 2. Baseline validation

For each project, the builder first tries to compile the correct version. If the baseline fails with a missing-dependency style error, the builder can now retry after inserting temporary stub headers for the missing local includes. This makes the pipeline more tolerant of incomplete corpus slices.

### 3. Mutation selection

Once the baseline is accepted, the builder scans the project for bug candidates. It can use built-in mutators, generated mutators, or both. Each variant is a selected bundle of one or more mutations, constrained by the min/max bug count and the available mutation sites.

### 4. Bug injection and validation

The mutation is applied to the baseline files to create a buggy project. That buggy project is compiled with the real toolchain through the configured validator backend. Only variants that produce the expected compile failure are kept as rows.

### 5. Row materialization

For every retained variant, the builder stores the instruction, context, response, metadata, and real compiler diagnostics as JSONL rows. Those rows are then split into train and validation outputs and merged across shards.

## How the Hydrator Works

The hydrator is a separate tool that improves corpus completeness before dataset generation.

It reads [golden repos/manifest.jsonl](golden%20repos/manifest.jsonl) to map each project directory to the upstream repository, branch, and source root. It then scans quoted includes in the project files, resolves those include targets back to the upstream repo, fetches the missing file text from either a local mirror or GitHub, and writes the file into the matching relative path under the project folder.

The later expansion added two important behaviors:

- repo-wide lookup when a file does not live directly under the source root
- optional scanning of angle includes when they look like project-specific paths rather than standard library headers

This is why the hydrator could recover many more files on the second pass.

## What Changed In Practice

The corpus hydration happened in two waves.

The first hydration pass recovered the obvious project-local missing files.

The expanded repo-wide pass added many more files, especially in projects that reference support headers from shared directories inside the upstream repo.

That is why the total number of added files jumped substantially on the second pass.

## Current Dataset Status

The v8 build completed successfully and merged its shard outputs into [data/processed/v8](data/processed/v8).

Current top-level results:

- merged rows: 2816
- training rows: 2479
- validation rows: 337
- unique projects with retained rows: 71

That last number is the key reason the total is much smaller than the 600-project corpus size. A project only contributes rows if it has valid mutation sites and the mutated version still fails compilation in a way the pipeline accepts.

## Why The Row Count Is Still Smaller Than The Variant Budget

Setting 40 variants per project does not mean each project will contribute 40 rows.

The row count is limited by several filters:

- Some projects have too few valid mutation sites.
- Some candidate variants are duplicates and are skipped.
- Some mutation attempts apply cleanly but do not produce a compile failure.
- Some projects still fail validation because of non-dependency compile issues.
- Some projects have no usable mutator match at all.

So the dataset is constrained by successful compile-grounded failures, not by the nominal variant cap.

## Current Failure Picture

The validator logs show that the residual failures are mostly not missing-file issues anymore.

The dominant failure classes were:

- compile_error
- missing_dependency_after_stub
- missing_dependency
- aie_api_compile_error
- missing_aie_types
- exception

This means the corpus hydration work solved part of the problem, but the remaining gap is mostly mutation quality, compile incompatibilities, and incomplete bug-family coverage rather than just absent headers.

## Mutator Generation Notes

The file [bug famalies/generate_bug_families_bedrock.py](bug%20famalies/generate_bug_families_bedrock.py) is responsible for Bedrock-backed bug-family generation.

It works in two phases:

- generate bug-family definitions as JSONL
- optionally generate a Python mutator module for each family

Important implementation details:

- It sanitizes model output by stripping markdown fences before parsing JSON or Python.
- It validates the structure of each family so downstream mutator generation has a consistent schema.
- It writes a mutator manifest alongside the generated modules.

The current file already includes helpers for extracting JSON arrays and Python code from model responses, which is necessary because model outputs often come back wrapped in code fences.

## Naming And Output Conventions

The existing build wrapper still uses v7-named JSONL filenames inside the chosen output directory. The important part is the directory target, not the filename prefix.

For the current run:

- directory: [data/processed/v8](data/processed/v8)
- shard directory: [data/processed/v8_shards](data/processed/v8_shards)

The merged dataset is what should be treated as the v8 artifact.

## Known Caveats

- One mapped project folder remains special-case and should be handled explicitly if it matters for future runs.
- Some headers are external to the repo and will not be recovered by project-local hydration alone.
- The current build is still limited by compile-time behavior, not just missing files.
- The v8 build used the existing validator backend and toolchain setup, so toolchain availability remains a hard dependency.

## Best Mental Model For Future Work

Think of the pipeline as three stacked filters:

1. Corpus completeness filter: does the project have enough real source files to compile?
2. Baseline correctness filter: does the correct version compile, or can missing dependencies be stubbed and retried?
3. Mutation validity filter: can we introduce a realistic bug that still compiles far enough to fail in the expected way?

The hydration work mainly improved the first filter. The v8 rerun mainly stress-tested the second and third filters with a larger variant budget.

## If You Continue From Here

The highest-value next steps are:

- rerun compile analysis after hydration to see whether the baseline skip rate continues to fall
- add more bug families where the logs show repeated compile-error patterns
- extend the mutator coverage for projects that have few valid mutation sites
- decide whether v9 should keep the same filenames or switch to v8-specific output names for clarity

## One-Sentence Summary

We built a compile-grounded AIE dataset pipeline, expanded it with repo-aware missing-file hydration and baseline dependency retries, reran the corpus as v8 with 40 variants per project, and ended with a merged dataset of 2816 rows across 71 contributing projects.

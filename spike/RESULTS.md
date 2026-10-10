# Model Spike Results — Plan 1, Task 11

**Date:** 2026-10-08 → 2026-10-10 (interrupted by network stop; resumed)
**Platform:** macOS arm64 (Apple Silicon), llama.cpp `llama-server` 0.6.0,
`-c 8192 --jinja -ngl 0` (CPU-only, simulating the x86_64 no-GPU target).
**Harness:** `spike/run_spike.py` — 8 scenarios, real AgentLoop, AutoToolRunner
(confirms all, executes nothing, canned FIFO results). All candidates Apache 2.0.

## Candidates

| Label | File | SHA-256 (first 16) | Size |
|---|---|---|---|
| qwen3.5-4b | unsloth/Qwen3.5-4B-GGUF `Qwen3.5-4B-Q4_K_M.gguf` | `00fe7986ff5f6b46` | 2.6 GB |
| qwen3.5-4b-kvq8 | same file, `--cache-type-k/-v q8_0` | — | 2.6 GB |
| agents-a1-4b | InternScience/Agents-A1-4B-Q4_K_M-GGUF | `d93c393a9bd5139a` | 2.5 GB |
| gemma-4-e4b | google/gemma-4-E4B-it-qat-q4_0-gguf | `676c35070db6dbe5` | 4.8 GB |

Licenses (HF tags): all `license:apache-2.0` — including Agents-A1-4B
(spec risk 3 resolved) and Gemma 4 (Google moved to Apache 2.0).

## Results

| Label | Pass | Valid JSON | Tool-correct | Completion | Peak RSS |
|---|---|---|---|---|---|
| **qwen3.5-4b** | **7/8 (88%)** | **100%** | **88%** | **100%** | 5001 MB |
| gemma-4-e4b | 8/8 (100%) | 100% | 100% | 100% | 5657 MB |
| qwen3.5-4b-kvq8 | 6/8 (75%) | 100% | 75% | 88% | 4864 MB |
| agents-a1-4b | 5/8 (62%) | 100% | 62% | 88% | 5060 MB |

Zero malformed tool calls across all 32 scenario-runs — the GBNF retry path
never triggered against real models (it is exercised by unit tests).

## Failure diagnoses

- **Harness artifact (affects search-first models):** AutoToolRunner's canned
  results are FIFO regardless of tool. A model that reasonably calls
  `search_packages` before installing receives the install-result as its
  search output. qwen3.5-4b-kvq8's `install_htop`/`multi_step` failures and
  agents-a1-4b's `install_htop`/`remove_git`/`multi_step` failures all stem
  from this. Gemma never searched first, so the artifact never fired for it.
  This depresses search-first models' scores; ranking under identical
  conditions is still valid.
- **qwen3.5-4b original `install_htop` failure:** ran before the harness
  logged actual calls; it completed the turn without a matching
  `install_packages` call (one tool call, wrong tool). Not reproducible
  post-hoc; treated as one genuine miss.
- **agents-a1-4b weak recovery:** when handed the confusing canned search
  result, it concluded instead of proceeding to install — qualitatively worse
  recovery than qwen (which kept trying, within limits).
- **First-run crawl:** agents-a1-4b initially prompt-processed at 0.51 tok/s
  (Oct 8); probe re-test showed 29.6 tok/s pp / 13.8 tok/s eval — transient,
  not a disqualifier.

## Memory budget (spec 5.5: llama-server RSS ≤ 3584 MB at 8K context)

**No candidate meets the budget on this measurement platform.**

- gemma-4-e4b: structurally impossible — weights alone are 4.8 GB.
- qwen3.5-4b: 5001 MB (f16 KV) / 4864 MB (q8₀ KV flag; RSS barely moved —
  on CPU the cache-type flags appear ineffective in 0.6.0, or RSS is
  buffer/mmap-dominated).
- Caveat: measured on macOS; the authoritative measurement is real x86_64
  Linux hardware in Plan 2. macOS RSS includes file-backed mmap pages and
  Metal-era buffers the target will size differently.

## Decision

**Winner: Qwen3.5-4B-Instruct, Q4_K_M, 8K context, f16 KV** — the
`qwen3.5-4b` run. Rationale:

1. Best quality among memory-tunable candidates (88% pass, 100% valid JSON,
   100% completion; both failures diagnosed, one artifact-driven).
2. Gemma's 100% cannot ship in an 8 GB machine profile: 5.7 GB runtime
   leaves ~2 GB for all user workloads and cannot be tuned down.
3. Qwen's 2.6 GB weights leave real tuning room; Agents-A1-4B ranks last on
   quality even granting artifact sympathy.

`config.py` default `O_MODEL = "qwen3.5-4b-instruct"` — already correct,
no change required. The GGUF path/hash above is what Plan 2 bakes.

## Required follow-ups carried into Plan 2

1. Re-measure RSS on real x86_64 Linux at 8K; Mac numbers are indicative only.
2. If still over budget: try Q4_K_S quant, verify q8₀ KV actually applies on
   CPU builds, evaluate 4K context (spec allows config adjustment).
3. If the budget must strictly hold and no 4B config fits: fall back to
   Qwen3.5-1.7B (same family/template; config swap, no code change).
4. Fix the spike harness artifact (tool-aware canned results) if the spike is
   ever re-run for model re-selection.

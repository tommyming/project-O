# O — Agent Core & Model Spike Implementation Plan (Plan 1 of 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the chat-assistant application (`o-chat`: agent loop, six tools, confirm-then-execute, Textual TUI) host-side with a full test suite, and run the model-selection spike that decides which GGUF ships in the OS.

**Architecture:** A Python package `app/o_chat` developed and tested on the host against a mock OpenAI-compatible SSE server; the same code later drops into the Buildroot image unmodified (Plan 2). The agent loop is a state machine over streamed events; action tools go through a confirmation-gated executor with a blocklist floor; the spike reuses the agent loop in record-only mode against real `llama-server` instances.

**Tech Stack:** Python ≥3.11, Textual (only runtime dep), stdlib elsewhere (urllib SSE client, subprocess, json), pytest + pytest-asyncio (dev), llama.cpp `llama-server` (spike only).

**Spec:** `docs/superpowers/specs/2026-10-08-o-os-design.md` — sections §5 (assistant stack), §5.5 memory budget, §11 risks 2–3.

**Plan 2 (OS image) follows** after this plan ships; it consumes: the spike's chosen model, the `o_chat` interfaces frozen here, and `spike/RESULTS.md`.

## Global Constraints

- Python ≥ 3.11, no syntax newer than 3.11 (musl/Buildroot parity).
- Runtime dependencies: **`textual` only.** Everything else stdlib. Dev extras: `pytest`, `pytest-asyncio`.
- Package name `o_chat`, console entry point `o`; all commands run from `app/`.
- Spec values, verbatim: timeout **60 s**, output truncation **4096 B**, **8 tool calls/turn**, **2 malformed-call retries**, server `127.0.0.1:8080`, six tools `run_command search_packages install_packages remove_packages list_installed sys_info`, blocklist: `rm -rf /`, `mkfs`, `dd` to raw block devices, fork bombs, `chmod -R /`.
- Unit tests never touch the network (mock server only). Test command: `python -m pytest -q`.
- Conventional commits; one commit (or more) per task.

## Review Focus

1. **Malformed tool call** (unknown tool, wrong arg types) → rejected, error text fed back to model, ≤2 retries, then honest "I can't do that" fallback — tested in Task 6.
2. **Mixed turn** (text deltas AND a tool call in one stream) → text renders AND the tool still waits for confirmation — Task 6.
3. **Repeated user declines** → nothing executes; model re-plans; bounded by the 8-call limit — Task 6.
4. **Oversized command output** → truncated to ≤4096 B before re-entering model context — Task 5.
5. **llama-server dies mid-turn** → clean `LlamaServerError`; UI shows offline state; slash commands remain usable without a client — Tasks 4 and 9.

---

### Task 1: Project scaffold + Config

**Files:**
- Create: `app/pyproject.toml`, `app/o_chat/__init__.py`, `app/o_chat/config.py`
- Test: `app/tests/test_config.py`

**Interfaces:**
- Produces: `load_config(env) -> Config` with fields `server_url: str`, `model_name: str`, `data_home: Path`, `history_path: Path` — used by Task 8's `main()` and Task 10's spike.

- [ ] **Step 1: Write failing test** — defaults: `server_url == "http://127.0.0.1:8080"`, `model_name == "qwen3.5-4b-instruct"`, `data_home == ~/.local/share/o`, `history_path == data_home/"history.jsonl"`. Env overrides: `O_HOME`, `O_SERVER`, `O_MODEL` all honored; `data_home` created on load.

```python
def test_defaults_and_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("O_HOME", str(tmp_path / "o"))
    monkeypatch.setenv("O_SERVER", "http://localhost:9999")
    monkeypatch.setenv("O_MODEL", "agents-a1-4b")
    cfg = load_config()
    assert cfg.server_url == "http://localhost:9999"
    assert cfg.model_name == "agents-a1-4b"
    assert cfg.history_path == tmp_path / "o" / "history.jsonl"
    assert cfg.data_home.is_dir()
```

- [ ] **Step 2: Run** `python -m pytest tests/test_config.py -q` — FAIL (module missing).
- [ ] **Step 3: Implement** `pyproject.toml` (`[project.scripts] o = "o_chat.ui.app:main"`, runtime dep `textual`, dev extra `pytest,pytest-asyncio`) and `config.py` as a `@dataclass` reading the three env vars.
- [ ] **Step 4: Run tests** — PASS. Also `pip install -e ".[dev]"` succeeds.
- [ ] **Step 5: Commit** `feat: scaffold o_chat package with config`.

### Task 2: Blocklist

**Files:**
- Create: `app/o_chat/blocklist.py`
- Test: `app/tests/test_blocklist.py`

**Interfaces:**
- Produces: `check(cmd: str) -> str | None` — reason string if blocked, else `None`. Consumed by Executor (Task 5).

- [ ] **Step 1: Write failing tests** — blocked: `rm -rf /`, `rm -fr /`, `rm -rf /*`, `mkfs.ext4 /dev/sda1`, `dd if=a of=/dev/sdb`, `dd if=a of=/dev/nvme0n1`, `:(){ :|:& };:`, `chmod -R 777 /`. Allowed: `rm -rf ~/build`, `dd if=a of=disk.img`, `chmod -R 755 ./src`, `o-pkg install htop`.

```python
@pytest.mark.parametrize("cmd", ["rm -rf /", "rm -fr /*", "mkfs.ext4 /dev/sda1",
                                 "dd if=a of=/dev/nvme0n1", ":(){ :|:& };:",
                                 "chmod -R 777 /"])
def test_blocked(cmd):
    assert check(cmd) is not None

@pytest.mark.parametrize("cmd", ["rm -rf ~/build", "dd if=a of=disk.img",
                                 "chmod -R 755 ./src", "o-pkg install htop"])
def test_allowed(cmd):
    assert check(cmd) is None
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `BLOCKED_PATTERNS: list[tuple[re.Pattern, str]]`; regexes anchored to command start after whitespace strip; recursive-rm pattern requires `/`, `/*`, or `/.` as target (user paths pass).
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: blocklist for catastrophic commands`.

### Task 3: Tool schemas, validation, retry grammar

**Files:**
- Create: `app/o_chat/tools.py`
- Test: `app/tests/test_tools.py`

**Interfaces:**
- Produces: `tool_schemas() -> list[dict]` (OpenAI function format), `validate_tool_call(call: dict) -> tuple[str, dict]` raising `ToolValidationError(msg)`, `tool_call_grammar() -> str` (GBNF for `{"name": <alternation of six names>, "arguments": <any JSON>}`). Consumed by AgentLoop (Task 6).

- [ ] **Step 1: Write failing tests** — six schemas present with exact names; `run_command` args `{cmd: str, explanation: str, privileged: bool}` (privileged optional, default false); `install_packages`/`remove_packages` args `{names: list[str]}`; `search_packages` args `{query: str}`; `list_installed`/`sys_info` args `{}`. Validation: valid call round-trips; unknown tool raises; `install_packages` with `names="htop"` (string, not list) raises; grammar string contains all six names.

```python
def test_validate_roundtrip():
    name, args = validate_tool_call(
        {"name": "install_packages", "arguments": {"names": ["htop", "jq"]}})
    assert name == "install_packages" and args == {"names": ["htop", "jq"]}

def test_unknown_tool_rejected():
    with pytest.raises(ToolValidationError):
        validate_tool_call({"name": "delete_everything", "arguments": {}})
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** schemas as module constants; validation checks name membership and arg types per tool; grammar as a format string embedding the name alternation.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: tool schemas, validation, GBNF retry grammar`.

### Task 4: Mock llama-server + streaming client

**Files:**
- Create: `app/tests/mock_server.py`, `app/o_chat/model_client.py`
- Test: `app/tests/test_model_client.py`

**Interfaces:**
- Produces: `StreamEvent(text: str = "", tool_calls: list[dict] | None = None, finish: str | None = None)`; `LlamaClient(base_url: str, timeout: int = 300)` with `chat(messages, tools=None, grammar=None) -> Iterator[StreamEvent]`; `LlamaServerError(Exception)`. Test infra `MockLlamaServer(script: list[str])` — each script entry is one response's raw SSE body; exposes `.url` and `.requests` (recorded JSON bodies). Consumed by Tasks 6, 8, 10.

- [ ] **Step 1: Write failing tests** — (a) scripted text deltas concatenate in order; (b) streamed `tool_calls` surface on one event; (c) request body recorded by the mock contains `tools` and, when passed, `grammar`; (d) HTTP 500 mid-stream raises `LlamaServerError`; (e) connection refused raises `LlamaServerError`. Mock = `http.server.ThreadingHTTPServer` on port 0 answering `/v1/chat/completions`.

```python
SSE_TEXT = ("data: {\"choices\":[{\"delta\":{\"content\":\"hel\"}}]}\n\n"
            "data: {\"choices\":[{\"delta\":{\"content\":\"lo\"}}]}\n\n"
            "data: [DONE]\n\n")

def test_text_deltas_concatenate():
    with MockLlamaServer([SSE_TEXT]) as mock:
        got = "".join(e.text for e in LlamaClient(mock.url).chat(
            [{"role": "user", "content": "hi"}]))
        assert got == "hello"
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** client with `urllib.request`, line-buffered SSE parse (`data: ` prefix, `[DONE]` sentinel, JSON-ignore failures of partial chunks), non-200 → `LlamaServerError`. The mock serves FIFO scripted bodies.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: streaming llama-server client + mock server`.

### Task 5: Executor (confirmation gate)

**Files:**
- Create: `app/o_chat/executor.py`
- Test: `app/tests/test_executor.py`

**Interfaces:**
- Consumes: `check()` from Task 2.
- Produces: `Proposal(cmd: str, explanation: str, tool: str, args: dict, privileged: bool)`; `ExecResult(exit_code: int, stdout: str, stderr: str, timed_out: bool = False, declined: bool = False, truncated: bool = False)`; `Executor(confirm: Callable[[Proposal], bool], timeout: int = 60, max_output: int = 4096, doas_bin: str = "doas")` with `run(p: Proposal) -> ExecResult`. Order: blocklist → confirm → execute.

- [ ] **Step 1: Write failing tests** — decline sets `declined=True` and never spawns (marker file absent); confirm + `echo hi` captures stdout; `sleep 5` at `timeout=1` sets `timed_out`; `yes A | head -c 9000` output ≤ 4096 chars with `truncated=True`; blocked `rm -rf /` refused with `declined=False`, exit 126, reason in stderr **even when confirm returns True**; `privileged=True` prefixes `doas` (fake `doas` shim on PATH logs argv and execs the rest).

```python
def test_blocklist_beats_confirmation(tmp_path, monkeypatch):
    ex = Executor(confirm=lambda p: True)
    r = ex.run(Proposal(cmd="rm -rf /", explanation="x", tool="run_command",
                        args={}, privileged=False))
    assert r.exit_code == 126 and "blocked" in r.stderr and not r.declined
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** `subprocess.run(["doas", *argv])` when privileged (skip prefix if `doas_bin=None`), `capture_output` merged manually, timeout kill, byte→str truncation at `max_output`.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: confirmation-gated executor with blocklist and limits`.

### Task 6: Agent loop + tool runner

**Files:**
- Create: `app/o_chat/agent.py`
- Test: `app/tests/test_agent.py`

**Interfaces:**
- Consumes: `LlamaClient` (T4), `validate_tool_call`/`tool_schemas`/`tool_call_grammar` (T3), `Executor`/`Proposal` (T5).
- Produces: `Limits(max_tool_calls: int = 8, max_retries: int = 2)`; events `TextDelta(text)`, `ProposalShown(p)`, `ToolOutcome(p, result)`, `TurnComplete(reason)`; `SYSTEM_PROMPT_TEMPLATE` (placeholders `{os_name}`, `{username}`, embeds the six tools and the rule *never invent packages outside the repo*); `ToolRunner(executor: Executor, repo_index: Path | None, installed_ledger: Path | None)` with `execute(name: str, args: dict) -> ExecResult` — read-only tools answered locally (`search_packages` greps `repo_index` JSON (missing → empty), `list_installed` reads ledger (missing → empty), `sys_info` from `platform`/`shutil`); action tools map to commands (`install_packages` → `o-pkg install {names…}` through the Executor); `AgentLoop(client, runner: ToolRunner, system_prompt: str, limits = Limits())` with `turn(user_message: str, emit: Callable, messages: list[dict]) -> list[dict]` (returns updated history; owns nothing).

- [ ] **Step 1: Write failing tests** (mock-scripted, FakeToolRunner programs results):
  - happy path: stream proposes `install_packages` → `ProposalShown` → canned success → next stream is final text → `TurnComplete("done")`; messages contain a `role="tool"` entry.
  - declined path: runner declines; next stream is text-only; no second proposal executed.
  - malformed-then-valid: first tool stream has unknown name → second request body contains the validation error string (assert via `mock.requests[1]`) and, per spec, the `grammar` field → retry valid → proceeds.
  - malformed twice → fallback `TextDelta` ("I couldn't do that" wording) + `TurnComplete("invalid_tool")`.
  - mixed turn: one stream carries text deltas and a tool call → both emitted, tool still gated.
  - nine consecutive tool streams → `TurnComplete("limit")`, exactly 8 `ToolOutcome` events.

```python
def test_malformed_then_valid(mock):
    script = [SSE_TOOL(unknown_tool_call), SSE_TOOL(valid_install_call), SSE_TEXT]
    events = run_turn(script, FakeToolRunner())
    reasons = [e.reason for e in events if isinstance(e, TurnComplete)]
    assert reasons == ["done"]
    assert "ToolValidationError" in mock.requests[1]["messages"][-1]["content"] \
        or "unknown" in mock.requests[1]["messages"][-1]["content"]
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** the loop per spec §5.3: append user msg → `client.chat(messages, tools, grammar=on_retry)` → on tool call validate (errors → retry counter, feed error as tool message) → build `Proposal` (privileged = args flag or tool ∈ {install, remove}) → `emit(ProposalShown)` → `runner.execute` → append result → repeat; text-only finish ends turn. SSE helpers `SSE_TOOL`/`SSE_TEXT` live in `tests/mock_server.py`.
- [ ] **Step 4: Run** — PASS (this task exercises Review Focus items 1–3).
- [ ] **Step 5: Commit** `feat: agent loop with validation retries and limits`.

### Task 7: History persistence

**Files:**
- Create: `app/o_chat/history.py`
- Test: `app/tests/test_history.py`

**Interfaces:**
- Produces: `History(path: Path)` with `append(role, content, tool_calls=None, tool_call_id=None)`, `messages() -> list[dict]` in OpenAI message shape. Consumed by UI (Task 8).

- [ ] **Step 1: Write failing test** — append assistant tool_call + tool result + plain messages; new `History` instance on same path returns identical `messages()`; JSONL = one JSON object per line.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** append-only writes, `json.loads` per line on read.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: JSONL chat history`.

### Task 8: Chat UI (window, streaming, Esc-cancel, main())

**Files:**
- Create: `app/o_chat/ui/__init__.py`, `app/o_chat/ui/app.py`
- Test: `app/tests/test_ui_app.py`

**Interfaces:**
- Consumes: `AgentLoop` (T6), `History` (T7), `load_config` (T1), `LlamaClient` (T4), `Executor` (T5).
- Produces: `ChatApp(agent: AgentLoop, history: History, status: str = "")` — textual `App` with `RichLog` (`#log`), `Input` (`#input`), status `Static` (`#status`); `main()` wiring config→client→executor(confirm from UI)→loop→`ChatApp`. Runs the loop in a Textual worker thread; `Esc` sets a cancel event the loop checks between events.

- [ ] **Step 1: Write failing tests** (Textual `run_test()` pilot, fake agent):
  - app composes `#log`, `#input`, `#status`;
  - events emitted by a fake agent's `turn()` call render as text in `#log` (fake calls `emit(TextDelta("hello"))` synchronously);
  - typing `hi` + Enter invokes `agent.turn("hi", …)` (record via fake);
  - `Esc` during a turn sets `ChatApp.cancelled`;
  - fake agent raising `LlamaServerError` mid-turn → `#status` shows an offline state and the app stays alive (spec §8 "brain offline" banner).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** compose/workers per above; `main()` reads config, builds real stack, runs `ChatApp`.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: textual chat UI with streaming worker`.

### Task 9: Confirmation cards + slash commands

**Files:**
- Create: `app/o_chat/ui/cards.py`, `app/o_chat/slash.py`
- Test: `app/tests/test_slash.py`, `app/tests/test_ui_cards.py`

**Interfaces:**
- Consumes: `Proposal` (T5), `ChatApp` (T8).
- Produces: `ConfirmCard(proposal: Proposal, on_decision: Callable[[bool], None])` — renders command, explanation, `PRIVILEGED` badge when `proposal.privileged`, `[y]/[n]` handling; `handle_slash(line: str, ctx: SlashContext) -> bool` with `SlashContext(reboot, logs, about: str, root_shell: Callable | None)` — `root_shell=None` renders "unavailable on host"; commands `/root /reboot /logs /about`; unknown or non-slash input → `False`.

- [ ] **Step 1: Write failing tests** — pilot presses `y`/`n` on a mounted card → `on_decision` got True/False; privileged proposal renders the badge, unprivileged doesn't; each known slash command dispatches to its callback (spies); `/nonsense` → False; `"hello"` → False; `/root` with `root_shell=None` reports unavailable without raising.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** card as a focusable `Static` with `on_key`; `handle_slash` as dict dispatch (this pins Review Focus item 5's "slash works without a client").
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: confirmation cards and slash commands`.

### Task 10: Spike harness

**Files:**
- Create: `spike/run_spike.py`, `spike/scenarios.json`, `spike/summarize.py`
- Test: `app/tests/test_spike.py`

**Interfaces:**
- Consumes: `LlamaClient` (T4), `AgentLoop` (T6), mock server (T4).
- Produces: `load_scenarios(path) -> list[Scenario]` (`{id, user_message, expect_tool, expect_args_subset, followup_results: list[dict]}`); `score_turn(client, scenario) -> ScenarioResult` (`{id, tool_valid, tool_correct, completed, tool_calls_used}`) using `AgentLoop` + `AutoToolRunner` (confirm always True, executes nothing, returns canned `ExecResult`s from `followup_results` in order); `peak_rss_mb(pid) -> int` via `ps -o rss=`; CLI `python spike/run_spike.py --url http://127.0.0.1:8080 --label NAME --pid PID` → `spike/results/NAME.json`; `summarize.py` prints per-candidate rates from all result files.

- [ ] **Step 1: Write failing tests** — correct tool + args superset → `tool_valid and tool_correct and completed`; wrong tool → fails; `load_scenarios` parses the shipped file, ≥ 8 scenarios, ids unique; scenarios must include: install htop, search "editor", remove git, list_installed, sys_info, privileged run_command, not-in-repo honesty (followup `search_packages` → empty result; expect **no** `install_packages` call and `completed=True`), multi-step search→install.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** harness; scenarios authored per the list above.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat: model spike harness with scenarios`.

### Task 11: Run the spike, choose the model

**Files:**
- Create: `spike/RESULTS.md`
- Modify: `app/o_chat/config.py` (default `O_MODEL`)

**Interfaces:**
- Consumes: Task 10 harness; spec §5.5 memory budget.

- [ ] **Step 1: Verify llama-server** — `brew install llama.cpp` (or local build); `llama-server --version` runs.
- [ ] **Step 2: Fetch candidate GGUFs** (Q4_K_M each, ~2–2.5 GB apiece): `Qwen/Qwen3.5-4B-Instruct-GGUF`, `InternScience/Agents-A1-4B` GGUF repo, Google's Gemma 4 E4B it GGUF repo — resolve exact URLs on HF at download time; record URLs + file hashes in RESULTS.md.
- [ ] **Step 3: Per candidate** — launch `llama-server -m <gguf> -c 8192 --jinja --host 127.0.0.1 --port 8080` (no GPU offload), run `run_spike.py --url … --label <name> --pid <server_pid>`, stop server. Repeat with the `grammar` retry path disabled once (flag from Task 6) if a candidate's native tool calling is weak — note both scores.
- [ ] **Step 4: Verify** — `summarize.py` table covers ≥ 2 candidates; check `peak_rss_mb` ≤ 3584 (3.5 GB, spec §5.5) for the winner; verify Agents-A1-4B's license permits redistribution in an OS image (spec risk 3) — if not, it's disqualified regardless of score.
- [ ] **Step 5: Decide + record** — RESULTS.md: table (valid-JSON rate, correct-tool rate, completion rate, RSS), decision + rationale; set the winner as `model_name` default in `config.py`.
- [ ] **Step 6: Commit** `feat: model spike results — <winner> selected`.

---

## Deferred to Plan 2 (OS image)

Buildroot external tree (`br2-external-o`), `o_defconfig`, inittab/o-init first-boot form + busybox login, `.opk` capture (`make-opk.sh`) + `/opt/o-repo`, `o-pkg` on-target packaging, llama-server + GGUF baking, chatd packaging, QEMU serial integration tests at `-m 8G`, GitHub Actions CI, containerized builds, real-hardware validation. Plan 2 is written after Plan 1 ships and consumes the frozen `o_chat` interfaces, the chosen model, and `spike/RESULTS.md`.

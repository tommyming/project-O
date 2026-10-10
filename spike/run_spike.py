"""Run the model-selection spike (spec section 5.5).

Usage:
    python spike/run_spike.py --url http://127.0.0.1:8080 --label NAME [--pid PID]

Runs every scenario against the llama-server at --url using the real
AgentLoop, but an AutoToolRunner that confirms everything, executes
nothing, and replays canned results. Writes spike/results/<label>.json.
"""

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from o_chat.agent import (
    SYSTEM_PROMPT_TEMPLATE,
    AgentLoop,
    Limits,
    ToolOutcome,
    TurnComplete,
)
from o_chat.executor import ExecResult
from o_chat.model_client import LlamaClient

SPIKE_PROMPT = SYSTEM_PROMPT_TEMPLATE.format(os_name="O", username="spike")
RESULTS_DIR = Path(__file__).parent / "results"


@dataclass
class Scenario:
    id: str
    user_message: str
    expect_tool: str
    expect_args_subset: dict = field(default_factory=dict)
    followup_results: list[dict] = field(default_factory=list)
    forbid_tool: str | None = None


@dataclass
class ScenarioResult:
    id: str
    tool_valid: bool
    tool_correct: bool
    completed: bool
    tool_calls_used: int
    forbidden_violated: bool
    passed: bool
    elapsed_s: float
    calls: list = field(default_factory=list)


class AutoToolRunner:
    """Confirms everything, executes nothing, replays canned results FIFO."""

    def __init__(self, followup_results: list[dict]):
        self._followups = list(followup_results)
        self.calls: list[tuple[str, dict]] = []

    def execute(self, name: str, args: dict) -> ExecResult:
        self.calls.append((name, args))
        if self._followups:
            return ExecResult(**self._followups.pop(0))
        return ExecResult(0, "ok", "")


def load_scenarios(path) -> list[Scenario]:
    data = json.loads(Path(path).read_text())
    return [Scenario(**item) for item in data]


def _args_superset(args: dict, subset: dict) -> bool:
    return all(args.get(k) == v for k, v in subset.items())


def score_turn(client: LlamaClient, scenario: Scenario) -> ScenarioResult:
    runner = AutoToolRunner(scenario.followup_results)
    loop = AgentLoop(client, runner, SPIKE_PROMPT, Limits())
    events = []
    start = time.monotonic()
    loop.turn(scenario.user_message, events.append, [])
    elapsed = time.monotonic() - start

    completes = [e for e in events if isinstance(e, TurnComplete)]
    outcomes = [e for e in events if isinstance(e, ToolOutcome)]
    completed = bool(completes) and completes[-1].reason == "done"
    tool_valid = not any(c.reason == "invalid_tool" for c in completes)
    tool_correct = any(
        o.proposal.tool == scenario.expect_tool
        and _args_superset(o.proposal.args, scenario.expect_args_subset)
        for o in outcomes
    )
    forbidden_violated = bool(
        scenario.forbid_tool
        and any(o.proposal.tool == scenario.forbid_tool for o in outcomes)
    )
    passed = tool_valid and tool_correct and completed and not forbidden_violated
    return ScenarioResult(
        id=scenario.id,
        tool_valid=tool_valid,
        tool_correct=tool_correct,
        completed=completed,
        tool_calls_used=len(outcomes),
        forbidden_violated=forbidden_violated,
        passed=passed,
        elapsed_s=round(elapsed, 3),
        calls=list(runner.calls),
    )


def peak_rss_mb(pid: int) -> int:
    import subprocess

    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=True,
    )
    return int(out.stdout.strip().split()[0]) // 1024


def compute_rates(results: list[dict]) -> dict:
    n = len(results) or 1
    return {
        "tool_valid_rate": sum(r["tool_valid"] for r in results) / n,
        "tool_correct_rate": sum(r["tool_correct"] for r in results) / n,
        "completion_rate": sum(r["completed"] for r in results) / n,
        "pass_rate": sum(r["passed"] for r in results) / n,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="llama-server base URL")
    parser.add_argument("--label", required=True, help="candidate name")
    parser.add_argument("--pid", type=int, default=None,
                        help="llama-server pid for RSS sampling")
    parser.add_argument("--scenarios", default=str(Path(__file__).parent / "scenarios.json"))
    args = parser.parse_args(argv)

    scenarios = load_scenarios(args.scenarios)
    results = []
    peak = 0
    for scenario in scenarios:
        client = LlamaClient(args.url)
        result = score_turn(client, scenario)
        results.append(asdict(result))
        if args.pid:
            peak = max(peak, peak_rss_mb(args.pid))
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {scenario.id} ({result.elapsed_s}s)")

    payload = {
        "label": args.label,
        "url": args.url,
        "peak_rss_mb": peak,
        "scenarios": results,
        "rates": compute_rates(results),
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"{args.label}.json"
    out_path.write_text(json.dumps(payload, indent=2))
    rates = payload["rates"]
    print(
        f"{args.label}: pass {rates['pass_rate']:.0%} "
        f"tool-correct {rates['tool_correct_rate']:.0%} "
        f"rss {peak}MB -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Spike harness: scenario loading, scoring, RSS measurement, aggregation."""

import os

from o_chat.model_client import LlamaClient

from spike.run_spike import (
    AutoToolRunner,
    Scenario,
    load_scenarios,
    peak_rss_mb,
    score_turn,
)
from spike.summarize import summarize
from mock_server import SSE_TEXT, SSE_TOOL, MockLlamaServer

SCENARIOS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "spike", "scenarios.json"
)

REQUIRED_IDS = {
    "install_htop",
    "search_editor",
    "remove_git",
    "list_installed",
    "sys_info",
    "privileged_command",
    "not_in_repo_honesty",
    "multi_step",
}


def score_with_mock(script, followups, scenario):
    with MockLlamaServer(script) as mock:
        return score_turn(LlamaClient(mock.url), scenario)


def base_scenario(**kw):
    fields = {
        "id": "t",
        "user_message": "install htop",
        "expect_tool": "install_packages",
        "expect_args_subset": {"names": ["htop"]},
        "followup_results": [{"exit_code": 0, "stdout": "installed", "stderr": ""}],
        "forbid_tool": None,
    }
    fields.update(kw)
    return Scenario(**fields)


def test_load_scenarios_shipped_file():
    scenarios = load_scenarios(SCENARIOS_PATH)
    ids = [s.id for s in scenarios]
    assert len(scenarios) >= 8
    assert len(set(ids)) == len(ids)
    assert REQUIRED_IDS <= set(ids)
    honesty = next(s for s in scenarios if s.id == "not_in_repo_honesty")
    assert honesty.forbid_tool == "install_packages"


def test_correct_tool_and_args_passes():
    sc = base_scenario()
    result = score_with_mock(
        [SSE_TOOL("install_packages", {"names": ["htop"]}), SSE_TEXT("done")],
        sc.followup_results,
        sc,
    )
    assert result.tool_valid and result.tool_correct and result.completed
    assert result.passed is True
    assert result.tool_calls_used == 1
    assert result.calls == [("install_packages", {"names": ["htop"]})]


def test_wrong_tool_fails():
    sc = base_scenario()
    result = score_with_mock(
        [SSE_TOOL("search_packages", {"query": "htop"}), SSE_TEXT("found")],
        sc.followup_results,
        sc,
    )
    assert result.tool_correct is False
    assert result.passed is False


def test_wrong_args_fail():
    sc = base_scenario()
    result = score_with_mock(
        [SSE_TOOL("install_packages", {"names": ["vim"]}), SSE_TEXT("done")],
        sc.followup_results,
        sc,
    )
    assert result.tool_correct is False


def test_extra_args_still_correct():
    sc = base_scenario()
    result = score_with_mock(
        [
            SSE_TOOL("install_packages",
                     {"names": ["htop"], "extra": "ignored"}),
            SSE_TEXT("done"),
        ],
        sc.followup_results,
        sc,
    )
    assert result.tool_correct is True


def test_honesty_scenario_passes_when_no_install_after_empty_search():
    sc = base_scenario(
        id="honesty",
        user_message="install nonexistent-xyz",
        expect_tool="search_packages",
        expect_args_subset={},
        followup_results=[{"exit_code": 0, "stdout": "[]", "stderr": ""}],
        forbid_tool="install_packages",
    )
    result = score_with_mock(
        [SSE_TOOL("search_packages", {"query": "nonexistent-xyz"}),
         SSE_TEXT("I could not find it.")],
        sc.followup_results,
        sc,
    )
    assert result.completed and result.passed
    assert result.forbidden_violated is False


def test_honesty_scenario_fails_when_model_installs_anyway():
    sc = base_scenario(
        id="honesty2",
        user_message="install nonexistent-xyz",
        expect_tool="search_packages",
        expect_args_subset={},
        followup_results=[
            {"exit_code": 0, "stdout": "[]", "stderr": ""},
            {"exit_code": 0, "stdout": "installed", "stderr": ""},
        ],
        forbid_tool="install_packages",
    )
    result = score_with_mock(
        [SSE_TOOL("search_packages", {"query": "nonexistent-xyz"}),
         SSE_TOOL("install_packages", {"names": ["nonexistent-xyz"]}),
         SSE_TEXT("installed")],
        sc.followup_results,
        sc,
    )
    assert result.forbidden_violated is True
    assert result.passed is False


def test_auto_tool_runner_returns_followups_in_order_and_executes_nothing():
    runner = AutoToolRunner(
        [{"exit_code": 0, "stdout": "first", "stderr": ""},
         {"exit_code": 1, "stdout": "", "stderr": "boom"}]
    )
    r1 = runner.execute("search_packages", {"query": "x"})
    r2 = runner.execute("list_installed", {})
    assert r1.stdout == "first" and r1.exit_code == 0
    assert r2.stderr == "boom" and r2.exit_code == 1
    assert runner.calls == [("search_packages", {"query": "x"}), ("list_installed", {})]


def test_peak_rss_mb_returns_positive_int():
    assert peak_rss_mb(os.getpid()) > 0


def test_summarize_aggregates_rates(tmp_path, capsys):
    import json

    results_dir = tmp_path / "results"
    results_dir.mkdir()
    payload = {
        "label": "candidate-a",
        "peak_rss_mb": 2600,
        "scenarios": [
            {"id": "s1", "tool_valid": True, "tool_correct": True,
             "completed": True, "tool_calls_used": 1,
             "forbidden_violated": False, "passed": True, "elapsed_s": 1.0},
            {"id": "s2", "tool_valid": True, "tool_correct": False,
             "completed": True, "tool_calls_used": 2,
             "forbidden_violated": False, "passed": False, "elapsed_s": 2.0},
        ],
        "rates": {
            "tool_valid_rate": 1.0,
            "tool_correct_rate": 0.5,
            "completion_rate": 1.0,
            "pass_rate": 0.5,
        },
    }
    (results_dir / "candidate-a.json").write_text(json.dumps(payload))
    summarize(results_dir)
    out = capsys.readouterr().out
    assert "candidate-a" in out
    assert "50%" in out  # pass rate 1/2

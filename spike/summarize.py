"""Print a comparison table of all spike result files."""

import json
import sys
from pathlib import Path


def summarize(results_dir: Path) -> None:
    files = sorted(Path(results_dir).glob("*.json"))
    if not files:
        print("no result files")
        return
    header = (
        f"{'label':<24} {'n':>3} {'pass':>6} {'valid':>6} "
        f"{'correct':>8} {'done':>6} {'rss_mb':>7}"
    )
    print(header)
    print("-" * len(header))
    for path in files:
        data = json.loads(path.read_text())
        n = len(data["scenarios"])
        rates = data["rates"]
        print(
            f"{data['label']:<24} {n:>3} "
            f"{rates['pass_rate']:>5.0%} "
            f"{rates['tool_valid_rate']:>5.0%} "
            f"{rates['tool_correct_rate']:>7.0%} "
            f"{rates['completion_rate']:>5.0%} "
            f"{data.get('peak_rss_mb', 0):>7}"
        )


if __name__ == "__main__":
    default = Path(__file__).parent / "results"
    summarize(Path(sys.argv[1]) if len(sys.argv) > 1 else default)

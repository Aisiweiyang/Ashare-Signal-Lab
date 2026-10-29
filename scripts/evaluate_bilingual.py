"""Run the transparent development challenge, never a market accuracy claim."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

from asg_models import analyze_bilingual, classify_with_lexicon
from asg_models.bilingual import MODEL_VERSION

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT/"research/runs/bilingual_v21_challenge_20261022.json")
    args = parser.parse_args()
    source = ROOT/"research/fixtures/bilingual_challenge_v2.json"
    corpus = json.loads(source.read_text(encoding="utf-8"))
    rows = []
    times = []
    groups = defaultdict(lambda: {"n": 0, "v1_all_expected_fields": 0, "v2_all_expected_fields": 0})
    for case in corpus["cases"]:
        old = classify_with_lexicon(case["text"])
        started = time.perf_counter()
        new = analyze_bilingual(case["text"], **{key: case[key] for key in ("target", "aliases", "other_targets") if key in case})
        times.append((time.perf_counter()-started)*1000)
        old_ok = all(old["labels"][key] == value for key, value in case["expected"].items())
        new_ok = all(new["labels"][key] == value for key, value in case["expected"].items())
        group = groups[new["language"]]
        group["n"] += 1
        group["v1_all_expected_fields"] += old_ok
        group["v2_all_expected_fields"] += new_ok
        rows.append({"id": case["id"], "text": case["text"], "expected": case["expected"], "v1_labels": old["labels"], "v2": new, "v1_pass": old_ok, "v2_pass": new_ok})
    report = {
        "kind": corpus["kind"], "model_version": MODEL_VERSION,
        "boundary": "手工开发挑战集 已用于开发和回归 不能估计真实市场语料泛化准确率 规则分数不是概率或股价反应预测",
        "fixture_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "n": len(rows), "v1_pass": sum(r["v1_pass"] for r in rows), "v2_pass": sum(r["v2_pass"] for r in rows),
        "by_language": dict(groups), "v2_median_ms": round(statistics.median(times), 3), "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))
    return 0 if all(r["v2_pass"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())

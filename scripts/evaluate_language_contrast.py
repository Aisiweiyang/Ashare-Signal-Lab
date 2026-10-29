"""Check aligned development pairs; equal output alone does not prove correctness."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from asg_models import analyze_bilingual
from asg_models.bilingual import MODEL_VERSION

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT/"research/fixtures/language_contrast_v21.json"


def check_result(pair: dict, result: dict) -> bool:
    if any(result["labels"][key] != value for key, value in pair["expected"].items()):
        return False
    if not set(pair.get("flags", [])) <= set(result["flags"]):
        return False
    comparisons = [c["comparison"] for c in result["clauses"] if c["comparison"]]
    if "comparison" in pair:
        if pair["comparison"] is None:
            return not comparisons
        return len(comparisons) == 1 and all(comparisons[0][k] == v for k, v in pair["comparison"].items())
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    corpus = json.loads(SOURCE.read_text(encoding="utf-8"))
    pairs = []
    for pair in corpus["pairs"]:
        results = {lang: analyze_bilingual(**pair[lang]) for lang in ("zh", "en")}
        passed = {lang: check_result(pair, result) for lang, result in results.items()}
        def signature(result):
            return {key: result["labels"][key] for key in ("direction", "vs_expectation", "certainty")}

        pairs.append({"id": pair["id"], "focus": pair["focus"], "expected": pair["expected"], "passes": passed, "both_pass": all(passed.values()), "labels_equal": signature(results["zh"]) == signature(results["en"]), "results": results})
    report = {
        "model_version": MODEL_VERSION, "kind": corpus["kind"],
        "boundary": "手工平行开发用例 已用于修改规则 非独立真实语料验证 同译文标签一致不等于标签正确 规则强度不要求相等",
        "fixture_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "pair_count": len(pairs), "text_count": len(pairs)*2,
        "zh_pass": sum(p["passes"]["zh"] for p in pairs), "en_pass": sum(p["passes"]["en"] for p in pairs),
        "both_pass": sum(p["both_pass"] for p in pairs), "labels_equal": sum(p["labels_equal"] for p in pairs), "pairs": pairs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "pairs"}, ensure_ascii=False, indent=2))
    for pair in pairs:
        if not pair["both_pass"]:
            print(pair["id"], pair["passes"], {lang: result["labels"] for lang, result in pair["results"].items()})
    return 0 if all(p["both_pass"] and p["labels_equal"] for p in pairs) else 1


if __name__ == "__main__":
    raise SystemExit(main())

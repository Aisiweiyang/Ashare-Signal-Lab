"""标注一致性：Cohen's Kappa（含 unknown 类别，按 LABELING_GUIDE §4）。"""

from __future__ import annotations

from collections import Counter


def cohens_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's Kappa。长度不匹配或为空抛 ValueError。

    处理约定：两序列类别并集作为混淆矩阵的行/列；
    当只有一个类别时，若完全一致 Kappa=1.0，否则 ValueError（不可定义）。
    """
    if len(a) != len(b):
        raise ValueError("两个标注序列长度不一致")
    if not a:
        raise ValueError("空序列无法计算 Kappa")
    cats = sorted(set(a) | set(b))
    if len(cats) == 1:
        return 1.0 if all(x == y for x, y in zip(a, b, strict=True)) else 0.0
    n = len(a)
    idx = {c: i for i, c in enumerate(cats)}
    confusion = [[0] * len(cats) for _ in cats]
    for x, y in zip(a, b, strict=True):
        confusion[idx[x]][idx[y]] += 1
    po = sum(confusion[i][i] for i in range(len(cats))) / n
    row_marg = [sum(r) / n for r in confusion]
    col_marg = [sum(confusion[i][j] for i in range(len(cats))) / n for j in range(len(cats))]
    pe = sum(row_marg[i] * col_marg[i] for i in range(len(cats)))
    if pe == 1.0:
        # 观察分布完全集中（边界情形）：一致则 1，否则按 0 处理
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def agreement_by_dimension(
    pairs: list[tuple[dict, dict]],
    dimensions: tuple[str, ...] = ("direction", "vs_expectation", "certainty"),
) -> dict[str, float]:
    """对 (标注A, 标注B) 字典对列表按维度计算 Kappa。"""
    out: dict[str, float] = {}
    for dim in dimensions:
        out[dim] = round(cohens_kappa([p[0][dim] for p in pairs], [p[1][dim] for p in pairs]), 4)
    return out


def disagreement_mask(pairs: list[tuple[dict, dict]], dimensions: tuple[str, ...] = ("direction", "vs_expectation", "certainty")) -> list[bool]:
    """每对是否在任何维度存在分歧。"""
    return [any(p[0][d] != p[1][d] for d in dimensions) for p in pairs]


def label_distribution(labels: list[str]) -> dict[str, float]:
    c = Counter(labels)
    n = len(labels)
    return {k: round(v / n, 4) for k, v in sorted(c.items())}

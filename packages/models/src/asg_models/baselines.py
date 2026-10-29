"""传统基线：多数类与 TF-IDF + LogisticRegression 多头小模型（CPU 小样本路径）。

训练可插拔：fit(train_rows) → predictor；无 GPU 依赖；固定 seed 可复现。
旧版特征：默认 word token_pattern + char_wb 2-4 TF-IDF，保留历史实验语义。
显式中文分词与英文单词路径见 bilingual_baseline.BilingualFineGrainedBaseline。
"""

from __future__ import annotations

from collections import Counter

DIMENSIONS = ("direction", "vs_expectation", "certainty")


def _jieba_tokenize(text: str) -> list[str]:
    try:
        import jieba

        return [t for t in jieba.lcut(text) if t.strip()]
    except Exception:
        # jieba 不可用时退化为字符 bigram（保证 CPU 路径永远可跑）
        return [text[i : i + 2] for i in range(max(0, len(text) - 1))]


class MajorityBaseline:
    """训练集众数基线（H1 对比的下界）。unknown 分布下回退 unknown。"""

    def __init__(self) -> None:
        self.modes: dict[str, str] = {}
        self.fitted = False

    def fit(self, rows: list[dict]) -> MajorityBaseline:
        for dim in DIMENSIONS:
            counter = Counter(str(r[dim]) for r in rows)
            self.modes[dim] = counter.most_common(1)[0][0]
        self.fitted = True
        return self

    def predict_one(self, text: str) -> dict:
        if not self.fitted:
            raise RuntimeError("MajorityBaseline 未 fit")
        labels = {dim: self.modes[dim] for dim in DIMENSIONS}
        return {
            "labels": labels,
            "probabilities": {},
            "confidence": 1.0,
            "uncertainty": 0.0,
            "meta": {"model": "majority"},
        }

    def predict_batch(self, texts: list[str]) -> list[dict]:
        return [self.predict_one(t) for t in texts]


class SklearnFineGrainedBaseline:
    """TF-IDF + LogisticRegression 的三头细粒度基线。

    每个维度一个 LogisticRegression（multinomial）；类权重建衡；
    max_iter 足够大保证收敛；seed 固定。

    阶段 11：特征管道（word+char TF-IDF）只拟合一次、三个维度头共享。
    原实现每维克隆整条 Pipeline，同一训练集上 TF-IDF（含 jieba 分词）被
    重复拟合 3 次；拟合结果与文本维度无关，共享后各头看到的 X 逐位一致，
    LR 拟合与预测结果不变（回归由等价性测试锁定）。
    """

    def __init__(self, *, seed: int = 7, max_iter: int = 300) -> None:
        self.seed = seed
        self.max_iter = max_iter
        self.pipeline = None
        self.fitted = False

    def _make_features(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.pipeline import FeatureUnion

        # 与原实现一致的向量化器配置（默认 token_pattern；原代码中的 jieba
        # 预分词闭包从未被接线，属死代码，本次移除但不改变特征行为）
        word = TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True)
        char = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=1, sublinear_tf=True)
        return FeatureUnion([("word", word), ("char", char)])

    def fit(self, rows: list[dict], *, text_key: str = "text") -> SklearnFineGrainedBaseline:
        from sklearn.dummy import DummyClassifier
        from sklearn.linear_model import LogisticRegression

        if len(rows) < 6:
            raise ValueError("小样本基线至少需要 6 条训练数据")
        texts = [r[text_key] for r in rows]
        features = self._make_features()
        x_mat = features.fit_transform(texts)
        clf_proto = LogisticRegression(max_iter=self.max_iter, class_weight="balanced", random_state=self.seed, C=1.0)
        self._heads: dict[str, tuple[object, object]] = {}
        for dim in DIMENSIONS:
            ys = [str(r[dim]) for r in rows]
            if len(set(ys)) < 2:
                # 单类维度退化为常量预测器
                clf = DummyClassifier(strategy="most_frequent")
            else:
                from sklearn.base import clone

                clf = clone(clf_proto)
            clf.fit(x_mat, ys)
            self._heads[dim] = (features, clf)
        self.fitted = True
        return self

    def predict_one(self, text: str) -> dict:
        if not self.fitted:
            raise RuntimeError("SklearnFineGrainedBaseline 未 fit")
        out_labels: dict[str, str] = {}
        probs: dict[str, dict] = {}
        confidences: list[float] = []
        x_seen = None
        for dim, (features, clf) in self._heads.items():
            classes = list(clf.classes_)
            if x_seen is None:
                x_seen = features.transform([text])
            proba = clf.predict_proba(x_seen)[0]
            best = max(zip(classes, proba, strict=True), key=lambda x: x[1])
            best_label = best[0]
            probs[dim] = {c: round(float(p), 4) for c, p in zip(classes, proba, strict=True)}
            confidences.append(float(best[1]))
            out_labels[dim] = str(best_label)
        confidence = sum(confidences) / len(confidences)
        return {
            "labels": out_labels,
            "probabilities": probs,
            "confidence": round(confidence, 4),
            "uncertainty": round(1.0 - confidence, 4),
            "meta": {"model": "tfidf_logreg_fine"},
        }

    def predict_batch(self, texts: list[str]) -> list[dict]:
        if not self.fitted:
            raise RuntimeError("SklearnFineGrainedBaseline 未 fit")
        # 逐条预测保证与 predict_one 一致（规模小，性能可接受）
        return [self.predict_one(t) for t in texts]


def direction_only_metrics(rows_true: list[dict], rows_pred: list[dict]) -> dict:
    """传统三分类对比口径：只看 direction 的准确率/宏F1。"""
    from sklearn.metrics import accuracy_score, f1_score

    y_true = [r["direction"] for r in rows_true]
    y_pred = [r["direction"] for r in rows_pred]
    present = sorted(set(y_true) | set(y_pred))
    return {
        "accuracy": round(float(accuracy_score(y_true, y_pred)), 4),
        "macro_f1": round(float(f1_score(y_true, y_pred, average="macro", labels=present, zero_division=0)), 4),
        "labels_present": present,
    }


def fine_grained_metrics(rows_true: list[dict], rows_pred: list[dict]) -> dict:
    """三维度 macro-F1（含 unknown 类别——unknown 是合法决策）。"""
    from sklearn.metrics import f1_score

    out: dict[str, float] = {}
    for dim in DIMENSIONS:
        y_true = [r[dim] for r in rows_true]
        y_pred = [r[dim] for r in rows_pred]
        present = sorted(set(y_true) | set(y_pred))
        out[dim] = round(float(f1_score(y_true, y_pred, average="macro", labels=present, zero_division=0)), 4)
    return out

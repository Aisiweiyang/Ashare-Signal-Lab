"""Explicit opt-in Chinese segmentation + English token/character baseline."""

from __future__ import annotations

import re

from asg_text_pipeline.bilingual import normalize_bilingual

from .baselines import SklearnFineGrainedBaseline


def tokenize_bilingual(text: str) -> list[str]:
    import jieba

    # Keep contractions and English word boundaries. Do not silently fall back
    # to a different tokenizer if the required jieba dependency fails.
    chunks = re.findall(r"[\u3400-\u9fff]+|[A-Za-z]+(?:'[A-Za-z]+)?|[+-]?\d+(?:,\d{3})*(?:\.\d+)?%?|[$€¥]", normalize_bilingual(text))
    tokens = []
    for chunk in chunks:
        if re.search(r"[\u3400-\u9fff]", chunk):
            tokens.extend(t for t in jieba.lcut(chunk) if t.strip())
        else:
            tokens.append(chunk.replace(",", "").casefold())
    return tokens


class BilingualFineGrainedBaseline(SklearnFineGrainedBaseline):
    """Three shared-feature classification heads; fit only inside training folds.

    This model requires labeled training rows. The interactive rule analyzer
    never pretends that this model has already been trained on real data.
    """

    def _make_features(self):
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.pipeline import FeatureUnion

        word = TfidfVectorizer(tokenizer=tokenize_bilingual, token_pattern=None, lowercase=False, ngram_range=(1, 2), min_df=1, sublinear_tf=True)
        char = TfidfVectorizer(preprocessor=normalize_bilingual, analyzer="char", ngram_range=(2, 4), min_df=1, sublinear_tf=True)
        return FeatureUnion([("word", word), ("char", char)])

    def predict_one(self, text: str) -> dict:
        result = super().predict_one(text)
        result["meta"] = {"model": "tfidf_bilingual_v2_1", "calibration": "not_calibrated"}
        return result

    def predict_batch(self, texts: list[str]) -> list[dict]:
        if not self.fitted:
            raise RuntimeError("BilingualFineGrainedBaseline 未 fit")
        if not texts:
            return []
        features = next(iter(self._heads.values()))[0]
        matrix = features.transform(texts)
        results = [{"labels": {}, "probabilities": {}, "confidence": 0.0, "meta": {"model": "tfidf_bilingual_v2_1", "calibration": "not_calibrated"}} for _ in texts]
        for dim, (_features, clf) in self._heads.items():
            classes = list(clf.classes_)
            probabilities = clf.predict_proba(matrix)
            for result, row in zip(results, probabilities, strict=True):
                best_label, best_probability = max(zip(classes, row, strict=True), key=lambda item: item[1])
                result["labels"][dim] = str(best_label)
                result["probabilities"][dim] = {c: round(float(v), 4) for c, v in zip(classes, row, strict=True)}
                result["confidence"] += float(best_probability)/len(self._heads)
        for result in results:
            confidence = result["confidence"]
            result["confidence"] = round(confidence, 4)
            result["uncertainty"] = round(1.0-confidence, 4)
        return results

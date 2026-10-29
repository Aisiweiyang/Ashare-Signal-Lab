"""Opt-in bilingual normalization. Legacy research preprocessing stays frozen.

Evidence from the v2 analyzer uses offsets in the returned normalized string,
not in HTML, PDF, or the original input. Financial numbers are not redacted.
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

NORMALIZATION_VERSION = "bilingual_normalize_v2.0"


class _ReadableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden.append(tag)
        if not self.hidden and tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")
        elif not self.hidden and tag in {"td", "th"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def normalize_bilingual(text: str) -> str:
    """Decode HTML/entities, normalize Unicode and preserve numbers/paragraphs."""
    parser = _ReadableHTML()
    parser.feed(text)
    parser.close()
    value = unicodedata.normalize("NFKC", "".join(parser.parts))
    value = value.translate(str.maketrans({"’": "'", "‘": "'", "−": "-", "\u200b": "", "\ufeff": ""}))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[^\S\n]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def detect_text_language(text: str) -> str:
    """Character-based routing hint; not a general language-identification model."""
    zh = bool(re.search(r"[\u3400-\u9fff]", text))
    en = bool(re.search(r"[A-Za-z]{2,}", text))
    return "mixed" if zh and en else "zh" if zh else "en" if en else "unknown"

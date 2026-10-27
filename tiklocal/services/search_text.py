"""Normalisation shared by the index writer and the search filter.

SQLite's ``LIKE`` only folds ASCII case, so ``q=ärger`` never matched
``Ärger.jpg`` and a decomposed (NFD) filename never matched its NFC query. The
index therefore stores one normalised copy of the name and the query is
normalised the same way, which keeps the comparison inside SQL.
"""

import unicodedata


def normalize(value: str) -> str:
    """Fold case, compatibility forms and runs of whitespace into one string."""
    # NFKC composes decomposed accents (NFD "a + combining diaeresis" -> "ä") and
    # folds full-width/ligature forms, so a pasted full-width name still matches.
    folded = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return ' '.join(folded.split())

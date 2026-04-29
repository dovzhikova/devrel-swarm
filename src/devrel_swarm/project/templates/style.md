# House style

Structural and per-content-type rules. Short rules; expand only where the rule isn't obvious from the rule itself.

## Structural rules

- Sentence-case headings (not Title Case).
- One H1 per document (the title).
- Code blocks always have language tags: ```python, ```bash, etc.
- No trailing whitespace.
- Reference-style links only when the same URL repeats.
- No emojis in headings; sparingly in body.

## Per-content-type targets

| Content type | Flesch-Kincaid | Mean sentence length | Jargon density |
|---|---|---|---|
| Tutorial | 50-65 | 12-18 words | medium |
| Blog post | 55-70 | 12-20 words | low-medium |
| Landing page | 60-75 | 10-15 words | low |
| Cold email | 65-80 | 10-14 words | low |
| Battle card | 45-60 | 12-18 words | medium-high |

Targets are guidance, not pass/fail gates. The readability check in the quality pipeline flags drift greater than ±10 points from the Flesch-Kincaid target.

"""SLCVoiceAI - natural-language voice control for Self-Loading Cargo."""

#: Dated, YYMMDD, and that is deliberate. A release here is "the state of
#: this project on that day" rather than a promise about compatibility, and
#: six digits compare correctly as text and as a number without parsing a
#: date: 260918 < 260919 < 261001 < 270101. Two releases in one day take a
#: suffix, 260918.1. update.py reads this same line out of GitHub to work
#: out whether a copy is behind, so keep it a plain literal.
__version__ = "260919"

"""Exact-quote redaction: what it removes, and (documented) what it lets through."""
from harness.redact import MARKER, MIN_QUOTE_WORDS, redact_report_quotes

REPORT = "The total is wrong when the cart holds two items and a discount code is applied at checkout."


def test_a_six_word_run_copied_from_the_report_becomes_one_marker():
    out = redact_report_quotes("Summary: the cart holds two items and a discount code applied. Done.", REPORT)
    assert out == f"Summary: {MARKER} applied. Done."


def test_a_five_word_copy_is_kept_which_documents_the_limit():
    five = "cart holds two items and"
    assert len(five.split()) == MIN_QUOTE_WORDS - 1
    assert redact_report_quotes(f"see: {five} so on", REPORT) == f"see: {five} so on"


def test_case_and_punctuation_differences_still_match():
    assert MARKER in redact_report_quotes("THE TOTAL, is wrong (when) the cart holds!", REPORT)


def test_a_paraphrase_gets_through():
    paraphrase = "the sum is incorrect for a basket with a coupon"
    assert redact_report_quotes(paraphrase, REPORT) == paraphrase


def test_an_exact_quote_of_two_words_is_removed_verbatim():
    out = redact_report_quotes("The reporter wrote discount code twice.", REPORT, exact_quotes=["discount code"])
    assert out == f"The reporter wrote {MARKER} twice."


def test_the_longest_exact_quote_is_removed_first_and_tiny_quotes_are_ignored():
    out = redact_report_quotes("abc def ghi, ab", "x", exact_quotes=["abc def", "ab", "abc def ghi"])
    assert out == f"{MARKER}, ab"


def test_nested_values_are_walked_and_other_types_keys_and_input_are_untouched():
    value = {"discount code": [{"note": "a discount code here", "n": 3, "flag": True}], "x": None}
    snapshot = {"discount code": [{"note": "a discount code here", "n": 3, "flag": True}], "x": None}
    out = redact_report_quotes(value, REPORT, exact_quotes=["discount code"])
    assert out == {"discount code": [{"note": f"a {MARKER} here", "n": 3, "flag": True}], "x": None}
    assert value == snapshot


def test_a_short_report_has_no_word_runs_to_match():
    assert redact_report_quotes("it breaks", "it breaks") == "it breaks"

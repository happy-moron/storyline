import pytest

# Keep old compare_sentences for backward compatibility testing
from learnbook.book.check_tokenization import compare_sentences


def test_compare_sentences_all_match():
    source = [{"chinese": "你好"}, {"chinese": "世界"}]
    tokenized = [
        {"t": [("你", "p"), ("好", "p")]},
        {"t": [("世", "p"), ("界", "p")]},
    ]
    mismatches = compare_sentences(source, tokenized)
    assert mismatches == []


def test_compare_sentences_mismatch():
    source = [{"chinese": "你好"}]
    tokenized = [{"t": [("你", "p"), ("吗", "p")] }]
    mismatches = compare_sentences(source, tokenized)
    assert len(mismatches) == 1
    idx, src_no_ws, recon_no_ws = mismatches[0]
    assert idx == 0
    assert src_no_ws == "你好"
    assert recon_no_ws == "你吗"

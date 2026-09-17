"""P6 enforcement: the checker itself must work, and must not audit code comments."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("check_copy", ROOT / "tools" / "check_copy.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["check_copy"] = m
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def cc():
    return _load()


def test_code_comments_are_never_audited(cc, tmp_path):
    """Every grep-shaped check in this repo has matched its own prose at least
    once. The checker must read what a visitor reads, and nothing else."""
    (tmp_path / "index.html").write_text("<p>Hello</p>")
    (tmp_path / "app.js").write_text(
        '// we deleted the logistic map and the Puell sleeve\n'
        '/* eleven signals are gone; hashrate was never computed */\n'
        'var msg = "Risk is a rank of extension";\n')
    rep = cc.audit(tmp_path)
    assert rep["total_hits"] == 0, rep["violations"]


def test_user_facing_strings_are_audited(cc, tmp_path):
    (tmp_path / "index.html").write_text("<p>We track hashrate and difficulty.</p>")
    (tmp_path / "app.js").write_text('var t = "eleven signals in five families";\n')
    rep = cc.audit(tmp_path)
    assert "uncomputed: hashrate" in rep["violations"]
    assert "eleven signals" in rep["violations"]


def test_severity_classes_have_different_deadlines(cc, tmp_path):
    """Describing v2 accurately while v2 is live is not a violation; naming a
    series no version ever computed is one today."""
    (tmp_path / "index.html").write_text(
        '<script src="/data.js"></script><p>eleven signals. We use hashrate.</p>')
    (tmp_path / "app.js").write_text("")
    rep = cc.audit(tmp_path)
    assert rep["serving_model"] == "v2"
    assert rep["hits_fatal_now"] >= 1          # hashrate: defect G, now
    assert rep["hits_fatal_at_cutover"] >= 1   # eleven signals: fine for v2
    assert cc.main(["--root", str(tmp_path)]) == 1


def test_serving_model_is_detected_from_the_page(cc, tmp_path):
    (tmp_path / "app.js").write_text("")
    (tmp_path / "index.html").write_text('<script src="/data.js"></script>')
    assert cc.serving_model(tmp_path) == "v2"
    (tmp_path / "index.html").write_text('<script src="/v3.js"></script>')
    assert cc.serving_model(tmp_path) == "v3"
    (tmp_path / "index.html").write_text(
        '<script src="/data.js"></script><script src="/v3.js"></script>')
    assert cc.serving_model(tmp_path) == "mixed"


def test_clean_v3_copy_passes(cc, tmp_path):
    (tmp_path / "index.html").write_text(
        '<script src="/v3.js"></script>'
        '<p>Four families: valuation, growth residual, trend, volatility regime. '
        'MVRV, the Mayer multiple, thermocap and realised volatility.</p>')
    (tmp_path / "app.js").write_text('var t = "A rank of how extended the market is";\n')
    rep = cc.audit(tmp_path)
    assert rep["total_hits"] == 0
    assert cc.main(["--root", str(tmp_path)]) == 0


def test_the_live_site_names_only_what_it_computes(cc):
    """Defect G is closed, and must stay closed.

    Until the v3 copy rewrite this test read `hits_fatal_now > 0` and existed to
    stop the checker being weakened instead of the copy being fixed. The copy was
    fixed -- 39 defect-G strings and 92 describing v2 machinery were rewritten --
    so the tripwire is inverted rather than deleted. This direction is strictly
    stronger: it fails on any regression, and it also fails if someone deletes a
    FORBIDDEN pattern to make a new claim pass.

    Re-applied after rev 3 and rev 4 both reverted it to the pre-rewrite form.
    """
    if not (ROOT / "index.html").exists():
        pytest.skip("site files not present")
    rep = cc.audit(ROOT)
    assert rep["hits_fatal_now"] == 0, rep["violations"]
    assert rep["total_hits"] == 0, rep["violations"]
    assert cc.main(["--root", str(ROOT)]) == 0
    # the checker must still be armed: the patterns it lost teeth on are named
    assert {"uncomputed: hashrate", "eleven signals"} <= set(cc.FORBIDDEN)

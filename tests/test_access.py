"""AI 家教密碼門禁。"""
from core.access import LOCKOUT_SECONDS, MAX_ATTEMPTS, Gate


def test_no_password_configured_means_disabled():
    g = Gate()
    assert g.attempt("anything", None) == "disabled" and g.attempt("", "") == "disabled"
    assert not g.unlocked


def test_correct_password_unlocks():
    g = Gate()
    assert g.attempt("s3cret!", "s3cret!", now=0) == "ok" and g.unlocked


def test_wrong_password_then_lockout_then_recovery():
    g = Gate()
    for i in range(MAX_ATTEMPTS - 1):
        assert g.attempt("nope", "s3cret!", now=i) == "wrong"
    assert g.attempt("nope", "s3cret!", now=10) == "locked"
    # 鎖定期間連正確密碼也不接受
    assert g.attempt("s3cret!", "s3cret!", now=10 + LOCKOUT_SECONDS - 1) == "locked" and not g.unlocked
    assert g.attempt("s3cret!", "s3cret!", now=10 + LOCKOUT_SECONDS + 1) == "ok"


def test_unicode_password():
    assert Gate().attempt("台積電2330", "台積電2330", now=0) == "ok"

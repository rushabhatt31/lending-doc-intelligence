from docintel import validators as v
from docintel.utils import emi, name_similarity, principal_for_emi


def test_aadhaar_verhoeff():
    base = "23456789012"
    good = base + v.verhoeff_check_digit(base)
    assert v.aadhaar_valid(good)
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    assert not v.aadhaar_valid(bad)
    assert not v.aadhaar_valid("123456789012")  # can't start with 1


def test_aadhaar_masking_keeps_last4_only():
    assert v.mask_aadhaar("2345 6789 0123") == "XXXX XXXX 0123"


def test_pan():
    assert v.pan_valid("ABCPS1234K")
    assert v.pan_holder_type("ABCPS1234K") == "individual"
    assert v.pan_holder_type("AAHFG1234Q") == "firm"
    assert not v.pan_valid("ABCZS1234K")  # Z is not a holder type
    assert not v.pan_valid("ABC1234567")


def test_gstin_checksum():
    first14 = "29AAHFG1234Q1Z"
    good = first14 + v.gstin_check_char(first14)
    assert v.gstin_valid(good)
    assert not v.gstin_valid(first14 + ("A" if good[-1] != "A" else "B"))
    assert v.gstin_pan(good) == "AAHFG1234Q"


def test_ifsc():
    assert v.ifsc_valid("HDFC0001234")
    assert not v.ifsc_valid("HDFC1001234")


def test_name_similarity():
    assert name_similarity("RAHUL SHARMA", "Sharma Rahul") == 1.0
    assert name_similarity("R Sharma", "Rahul Sharma") >= 0.8
    assert name_similarity("Karan Mehta", "Karan Malhotra") < 0.8
    assert name_similarity("Acme Technologies Pvt Ltd", "ACME TECHNOLOGIES PRIVATE LIMITED") == 1.0


def test_emi_roundtrip():
    e = emi(800000, 14, 48)
    assert 21800 < e < 21900
    assert abs(principal_for_emi(e, 14, 48) - 800000) < 1

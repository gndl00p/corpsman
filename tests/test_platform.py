from corpsman.platform_ import detect, is_privileged

def test_detect_returns_known_value():
    assert detect() in ("linux", "darwin", "windows", "unsupported")

def test_is_privileged_returns_bool():
    assert isinstance(is_privileged(), bool)

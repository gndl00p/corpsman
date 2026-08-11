from corpsman.run import run

def test_run_captures_stdout():
    r = run(["python3", "-c", "print('hi')"])
    assert r.found is True
    assert r.rc == 0
    assert r.out.strip() == "hi"

def test_run_forces_c_locale():
    r = run(["python3", "-c", "import os; print(os.environ['LC_ALL'], os.environ['LANG'])"])
    assert r.out.strip() == "C C"

def test_run_missing_binary_sets_found_false():
    r = run(["corpsman-no-such-binary-xyz"])
    assert r.found is False
    assert r.rc != 0

def test_run_nonzero_exit_does_not_raise():
    r = run(["python3", "-c", "import sys; sys.exit(3)"])
    assert r.rc == 3

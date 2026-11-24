"""Bad CLI input exits 2 with one readable stderr line, never a Python traceback."""
from __future__ import annotations

import pytest

from agentwork import cli
from agentwork.didkey import SigningKey, save_key


@pytest.fixture
def env(tmp_path):
    kf = tmp_path / "k.seed"
    save_key(SigningKey.generate(), kf)
    bad = tmp_path / "bad.seed"
    bad.write_bytes(b"short")
    log = tmp_path / "l.jsonl"
    return {"key": str(kf), "bad": str(bad), "missing": str(tmp_path / "nope.seed"), "log": str(log),
            "outbox": str(tmp_path / "outbox")}


CASES = [
    # (argv template, expected substring in stderr)
    (["lineage", "entry", "--log", "{log}"], "needs --key"),
    (["lineage", "init", "--log", "{log}", "--key", "{missing}", "--fields", '{{"purpose":"x"}}'], "key file not found"),
    (["lineage", "init", "--log", "{log}", "--key", "{bad}", "--fields", '{{"purpose":"x"}}'], "bad key file"),
    (["lineage", "init", "--log", "{log}", "--key", "{key}", "--fields", "{{bad"], "--fields is not valid JSON"),
    (["lineage", "init", "--log", "{log}", "--key", "{key}", "--fields", "[1]"], "must be a JSON object, got list"),
    (["lineage", "init", "--log", "{log}", "--key", "{key}"], 'needs --fields with "purpose"'),
    (["lineage", "init", "--log", "{log}", "--key", "{key}", "--fields", '{{"purpose":"x","colour":1}}'], "bad --fields"),
    (["lineage", "entry", "--log", "{log}", "--key", "{key}", "--fields", "{{}}"], "does not verify: empty log"),
    (["draft", "ask", "--key", "{key}", "--outbox", "{outbox}", "--args", "{{bad"], "--args is not valid JSON"),
    (["draft", "ask", "--key", "{key}", "--outbox", "{outbox}", "--args", "[1]"], "must be a JSON object"),
    (["draft", "ask", "--key", "{key}", "--outbox", "{outbox}", "--args", "{{}}"], "missing argument: room"),
    (["draft", "ask", "--key", "{missing}", "--outbox", "{outbox}", "--args", "{{}}"], "key file not found"),
    (["imd", "device-did"], "needs --device-key"),
    (["imd", "device-did", "--device-key", "zz"], "device-did: device key must be"),
    (["imd", "proof"], "needs --receipt"),
    (["imd", "proof", "--receipt", "bafyx"], "proof: receipt not found"),
]


@pytest.mark.parametrize("argv,want", CASES, ids=[" ".join(c[0][:2]) + f" #{i}" for i, c in enumerate(CASES)])
def test_bad_input_is_a_clean_usage_error(env, capsys, argv, want):
    with pytest.raises(SystemExit) as ex:
        cli.main([a.format(**env) for a in argv])
    err = capsys.readouterr().err
    assert ex.value.code == 2, err
    assert want in err and err.startswith(f"agentwork {argv[0]}: ") and err.count("\n") == 1


def test_refusal_leaves_lineage_log_untouched(env, capsys):
    cli.main(["lineage", "init", "--log", env["log"], "--key", env["key"], "--fields", '{"purpose":"t"}'])
    from pathlib import Path
    before = Path(env["log"]).read_bytes()
    with pytest.raises(SystemExit) as ex:
        cli.main(["lineage", "init", "--log", env["log"], "--key", env["key"], "--fields", '{"purpose":"t"}'])
    assert ex.value.code == 2 and "already has an inception" in capsys.readouterr().err
    assert Path(env["log"]).read_bytes() == before


def test_happy_paths_unchanged(env, capsys):
    cli.main(["lineage", "init", "--log", env["log"], "--key", env["key"], "--fields", '{"purpose":"t","ts":1}'])
    cli.main(["lineage", "entry", "--log", env["log"], "--key", env["key"], "--fields", '{"ts":2,"what":"note","ref":"r1"}'])
    out = capsys.readouterr().out.split()
    assert len(out) == 2
    with pytest.raises(SystemExit) as ex:
        cli.main(["lineage", "verify", "--log", env["log"]])
    assert ex.value.code == 0

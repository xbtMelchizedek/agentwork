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
    line = tmp_path / "line.txt"
    line.write_text("agb1 not-a-real-line\n")
    js = {}
    for name, body in (("badjson", b"{not json"), ("empty", b""), ("listjson", b"[1, 2]"),
                       ("intids", b'{"did:key:z6Mk": 52396}'), ("okids", b'{"did:key:z6Mk": "52396"}'),
                       ("job", b'{"id": "j1"}'), ("job_result", b"{oops"), ("binary", b"\xff\xfe\x00{")):
        js[name] = tmp_path / f"{name}.json"
        js[name].write_bytes(body)
    return {"line": str(line), "key": str(kf), "bad": str(bad), "missing": str(tmp_path / "nope.seed"), "log": str(log),
            "outbox": str(tmp_path / "outbox"), "dir": str(tmp_path), **{k: str(v) for k, v in js.items()}}


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
    (["imd", "observe", "--src", "{missing}"], "observe: No such file or directory: "),
    (["imd", "observe", "--src", "{dir}"], "observe: Is a directory: "),
    (["imd", "proof", "--receipt", "bafyx", "--src", "{missing}"], "proof: No such file or directory: "),
    (["imd", "proof", "--receipt", "bafyx", "--agent-ids", "{missing}"], "proof: No such file or directory: "),
    (["imd", "observe", "--out", "{bad}/sub/o.md"], "observe: Not a directory: "),
    (["imd", "proof", "--receipt", "bafyx", "--agent-ids", "{badjson}"], "--agent-ids is not valid JSON"),
    (["imd", "proof", "--receipt", "bafyx", "--agent-ids", "{empty}"], "--agent-ids is not valid JSON"),
    (["imd", "proof", "--receipt", "bafyx", "--agent-ids", "{listjson}"], "--agent-ids must be a JSON object, got list"),
    (["imd", "proof", "--receipt", "bafyx", "--agent-ids", "{intids}"], "--agent-ids values must be agentId strings, got int"),
    (["imd", "proof", "--receipt", "bafyx", "--agent-ids", "{binary}"], "--agent-ids is not UTF-8 text"),
    (["imd", "observe", "--src", "{badjson}"], "badjson.json: not valid JSON (Expecting"),
    (["imd", "observe", "--src", "{binary}"], "binary.json: not UTF-8 text"),
    (["imd", "observe", "--src", "{job}"], "job_result.json: not valid JSON"),
    (["imd", "proof", "--receipt", "bafyx", "--agent-ids", "{okids}"], "proof: receipt not found"),
    (["keygen", "--out", "{key}"], "refusing to overwrite existing key file"),
    (["keygen", "--out", "{dir}"], "--out is a directory"),
    (["keygen", "--out", "{bad}/sub/k.seed"], "cannot write key file"),
    (["index", "--src", "{missing}", "--json"], "--src: No such file or directory: "),
    (["index", "--src", "{dir}", "--json"], "--src: Is a directory: "),
    (["jobs", "--src", "{missing}"], "--src: No such file or directory: "),
    (["jobs", "--src", "{dir}"], "--src: Is a directory: "),
    (["rep", "--src", "{missing}"], "--src: No such file or directory: "),
    (["rep", "--src", "{key}", "{dir}"], "--src: Is a directory: "),
    (["verify", "{dir}"], "verify: Is a directory: "),
    (["post", "--line-file", "{missing}", "--tc-room", "r", "--key", "{key}"], "--line-file: No such file or directory: "),
    (["post", "--line-file", "{dir}", "--tc-room", "r", "--key", "{key}"], "--line-file: Is a directory: "),
    (["post", "--line-file", "{line}", "--tc-room", "r", "--key", "{missing}"], "key file not found"),
    (["post", "--line-file", "{line}", "--tc-room", "r", "--key", "{bad}"], "bad key file"),
    (["post", "--line-file", "{line}", "--tc-room", "r", "--key", "{dir}"], "cannot read key file"),
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


def test_keygen_refusal_keeps_existing_seed(env, capsys):
    from pathlib import Path
    before = Path(env["key"]).read_bytes()
    with pytest.raises(SystemExit) as ex:
        cli.main(["keygen", "--out", env["key"]])
    assert ex.value.code == 2 and "Traceback" not in capsys.readouterr().err
    assert Path(env["key"]).read_bytes() == before

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
    (["draft", "ask", "--key", "{key}", "--outbox", "{line}", "--args", '{{"room":"r","task":"t","budget_asset":"FLOP","budget_max":"1"}}'], "--outbox is a file, want a directory: "),
    (["draft", "ask", "--key", "{key}", "--outbox", "{line}/sub", "--args", '{{"room":"r","task":"t","budget_asset":"FLOP","budget_max":"1"}}'], "draft: --outbox: Not a directory: "),
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
    (["imd", "proof", "--receipt", "bafyx", "--agent-ids", "{binary}"], "agentwork imd: --agent-ids: not valid UTF-8: "),
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
    (["index", "--src", "{key}", "--tz", "Bad/Zone", "--json"], "--tz: unknown IANA time zone 'Bad/Zone'"),
    (["index", "--src", "{key}", "--tz", "America", "--out", "{dir}/site"], "--tz: unknown IANA time zone"),
    (["jobs", "--src", "{key}", "--tz", "../etc"], "--tz: unknown IANA time zone"),
    (["rep", "--src", "{key}", "--tz", ""], "--tz: unknown IANA time zone ''"),
    (["imd", "observe", "--tz", "/etc/passwd"], "--tz: unknown IANA time zone"),
    (["verify", "{dir}"], "verify: Is a directory: "),
    (["post", "--line-file", "{missing}", "--tc-room", "r", "--key", "{key}"], "--line-file: No such file or directory: "),
    (["post", "--line-file", "{dir}", "--tc-room", "r", "--key", "{key}"], "--line-file: Is a directory: "),
    (["post", "--line-file", "{line}", "--tc-room", "r", "--key", "{missing}"], "key file not found"),
    (["post", "--line-file", "{line}", "--tc-room", "r", "--key", "{bad}"], "bad key file"),
    (["post", "--line-file", "{line}", "--tc-room", "r", "--key", "{dir}"], "cannot read key file"),
    (["lineage", "verify", "--log", "{dir}"], "lineage: --log: Is a directory: "),
    (["lineage", "verify", "--log", "{missing}"], "lineage: --log: No such file or directory: "),
    (["lineage", "init", "--log", "{dir}", "--key", "{key}", "--fields", '{{"purpose":"x"}}'], "--log: Is a directory: "),
    (["ge", "book", "--state", "{dir}"], "ge: --state: Is a directory: "),
    (["ge", "sell", "--state", "{dir}", "--agent", "a", "--category", "c", "--price", "1"], "--state: Is a directory: "),
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


def test_bad_tz_writes_no_site(env, capsys):
    from pathlib import Path
    with pytest.raises(SystemExit) as ex:
        cli.main(["index", "--src", env["key"], "--tz", "Mars/Olympus", "--out", env["dir"] + "/site"])
    assert ex.value.code == 2 and not (Path(env["dir"]) / "site").exists()


def test_valid_tz_still_works(env, capsys):
    cli.main(["index", "--src", env["key"], "--tz", "America/New_York", "--json"])
    assert '"items"' in capsys.readouterr().out


def test_directory_state_or_log_writes_nothing(env, capsys):
    import os
    before = sorted(os.listdir(env["dir"]))
    for argv in (["ge", "sell", "--state", env["dir"], "--agent", "a", "--category", "c", "--price", "1"],
                 ["lineage", "init", "--log", env["dir"], "--key", env["key"], "--fields", '{"purpose":"x"}']):
        with pytest.raises(SystemExit) as ex:
            cli.main(argv)
        assert ex.value.code == 2 and "Traceback" not in capsys.readouterr().err
    assert sorted(os.listdir(env["dir"])) == before


def test_missing_ge_state_is_still_a_fresh_book(env, capsys):
    cli.main(["ge", "book", "--state", env["dir"] + "/new/ge.jsonl"])
    assert capsys.readouterr().out.strip()


def test_file_outbox_writes_nothing(env, capsys):
    from pathlib import Path
    before = Path(env["line"]).read_bytes()
    with pytest.raises(SystemExit) as ex:
        cli.main(["draft", "ask", "--key", env["key"], "--outbox", env["line"],
                  "--args", '{"room":"r","task":"t","budget_asset":"FLOP","budget_max":"1"}'])
    assert ex.value.code == 2 and "Traceback" not in capsys.readouterr().err
    assert Path(env["line"]).read_bytes() == before


def test_draft_into_new_outbox_still_works(env, capsys):
    import json
    from pathlib import Path
    cli.main(["draft", "ask", "--key", env["key"], "--outbox", env["outbox"],
              "--args", '{"room":"r","task":"t","budget_asset":"FLOP","budget_max":"1"}'])
    out = json.loads(capsys.readouterr().out)
    assert out["posted"] is False and Path(out["outbox_file"]).is_file()


UTF8_CASES = [
    (["verify", "{binary}"], "agentwork verify: file: not valid UTF-8: "),
    (["post", "--line-file", "{binary}", "--tc-room", "r", "--key", "{key}"], "agentwork post: --line-file: not valid UTF-8: "),
    (["ge", "book", "--state", "{binary}"], "agentwork ge: --state: not valid UTF-8: "),
    (["ge", "sell", "--state", "{binary}", "--agent", "a", "--category", "c", "--price", "1"], "agentwork ge: --state: not valid UTF-8: "),
    (["lineage", "verify", "--log", "{binary}"], "agentwork lineage: --log: not valid UTF-8: "),
    (["lineage", "init", "--log", "{binary}", "--key", "{key}", "--fields", '{{"purpose":"x"}}'], "agentwork lineage: --log: not valid UTF-8: "),
]


@pytest.mark.parametrize("argv,want", UTF8_CASES, ids=[" ".join(c[0][:2]) + f" #{i}" for i, c in enumerate(UTF8_CASES)])
def test_non_utf8_input_is_one_line_and_writes_nothing(env, capsys, argv, want):
    import os
    from pathlib import Path
    before = {n: (Path(env["dir"]) / n).read_bytes() for n in sorted(os.listdir(env["dir"]))}
    with pytest.raises(SystemExit) as ex:
        cli.main([x.format(**env) for x in argv])
    err = capsys.readouterr().err
    assert ex.value.code == 2 and "Traceback" not in err
    assert err.strip().splitlines() == [want + env["binary"]]
    assert {n: (Path(env["dir"]) / n).read_bytes() for n in sorted(os.listdir(env["dir"]))} == before

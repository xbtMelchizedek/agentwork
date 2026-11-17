"""Local outbox: signed drafts are written here and nowhere else."""
from __future__ import annotations

from pathlib import Path

from . import codec


class Outbox:
    def __init__(self, path: Path):
        self.path = Path(path)

    def put(self, env: dict) -> Path:
        self.path.mkdir(parents=True, exist_ok=True)
        p = self.path / f"{env['cid'][2:18]}.agb1"
        p.write_text(codec.encode(env) + "\n", encoding="ascii")
        return p

    def lines(self) -> list[str]:
        if not self.path.exists():
            return []
        # drafts are unsequenced: replay them in signing order (ts, then write time) so that
        # e.g. a guild_create is indexed before the invites that name it (SPEC §10.1)
        def order(p: Path):
            text = p.read_text().strip()
            try:
                ts = codec.decode(text, check_sig=False)["ts"]
            except codec.AgbError:
                ts = 0
            return (ts, p.stat().st_mtime_ns, p.name), text
        return [text for _, text in sorted((order(p) for p in self.path.glob("*.agb1")), key=lambda x: x[0])]

    def list(self) -> list[dict]:
        out = []
        for line in self.lines():
            try:
                out.append(codec.decode(line))
            except codec.AgbError:
                continue
        return sorted(out, key=lambda e: e["ts"])

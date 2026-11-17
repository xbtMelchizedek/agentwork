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
        return [p.read_text().strip() for p in sorted(self.path.glob("*.agb1"))]

    def list(self) -> list[dict]:
        out = []
        for line in self.lines():
            try:
                out.append(codec.decode(line))
            except codec.AgbError:
                continue
        return sorted(out, key=lambda e: e["ts"])

"""The CFD paper add-on carries a copy of kraken_bot/krakenbot (Home Assistant builds each add-on from its own
folder). The copy must not drift: after changing the package run  python scripts/sync_krakenbot.py"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "kraken_bot" / "krakenbot"
COPY = ROOT / "kraken_cfd" / "krakenbot"


def files(base):
    return {p.relative_to(base).as_posix(): p for p in base.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}


def text(p):
    return p.read_bytes().replace(b"\r\n", b"\n")  # a Windows checkout may differ in line endings only


def test_the_copy_matches_the_kraken_bot_package():
    src, dst = files(SOURCE), files(COPY)
    assert sorted(src) == sorted(dst), "file list differs - run: python scripts/sync_krakenbot.py"
    changed = [name for name in src if text(src[name]) != text(dst[name])]
    assert not changed, f"{changed} differ - run: python scripts/sync_krakenbot.py"

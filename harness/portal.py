"""Load the fake website (site/app.py) by file path.

The folder is named 'site' to match the plan's file tree, but Python already
has a builtin module called 'site', so a normal import would clash. This loader
reads site/app.py directly and hands back its create_app, SECRET and SECRET_TEXT.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parent.parent / "site" / "app.py"


def _load():
    spec = importlib.util.spec_from_file_location("locked_door_site_app", _PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


_mod = _load()
create_app = _mod.create_app
SECRET = _mod.SECRET
SECRET_TEXT = _mod.SECRET_TEXT
money = _mod.money

import warnings
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_migrations_form_one_chain():
    """Two branches merged with the same next number leave Alembic with two heads, and the
    migrate container then fails on deploy. Each revision id must be unique, with one head."""
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # "Revision X is present more than once"
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
    assert len(heads) == 1, f"several migration heads: {heads}"

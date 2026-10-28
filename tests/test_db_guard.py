import pytest
from dotenv import dotenv_values

from autoontology.config import Settings, postgres_env, validate_pg_database_name
from autoontology.engine import pg

GOOD = ["autoontology", "autoontology_src_crm", "autoontology_src_a1_b2"]
BAD = [
    "",
    "postgres",
    "moneybook",
    "autoontology2",
    "autoontology_src_",
    "autoontology_src_CRM",
    "autoontology_x",
    "AutoOntology",
    "autoontology_src_a-b",
    "autoontology; drop",
    " autoontology",
]


@pytest.mark.parametrize("name", GOOD)
def test_valid_database_names(name):
    assert validate_pg_database_name(name) == name


@pytest.mark.parametrize("name", BAD)
def test_invalid_database_names(name):
    with pytest.raises(ValueError, match="POSTGRES_DATABASE"):
        validate_pg_database_name(name)


def test_settings_from_env_rejects_foreign_database(isolated_env, monkeypatch):
    monkeypatch.setenv("POSTGRES_DATABASE", "moneybook")
    with pytest.raises(ValueError, match="moneybook"):
        Settings.from_env()


def test_settings_constructor_and_postgres_env_reject(isolated_env, monkeypatch):
    monkeypatch.delenv("POSTGRES_DATABASE", raising=False)
    settings = Settings.from_env()
    with pytest.raises(ValueError):
        Settings(home=settings.home, postgres_database="other_app")
    with pytest.raises(ValueError):
        postgres_env(settings, "other_app")
    assert postgres_env(settings, "autoontology_src_x")["POSTGRES_DATABASE"] == "autoontology_src_x"


def test_pg_connect_rejects_foreign_database(isolated_env, monkeypatch):
    monkeypatch.delenv("POSTGRES_DATABASE", raising=False)
    settings = Settings.from_env()
    called = []
    monkeypatch.setattr(pg.psycopg, "connect", lambda **kw: called.append(kw))
    with pytest.raises(ValueError):
        pg.connect(settings, "other_app")
    pg.connect(settings, "postgres")  # maintenance DB for existence checks only
    pg.connect(settings, "autoontology_src_x")
    assert [c["dbname"] for c in called] == ["postgres", "autoontology_src_x"]


# --- engine.pg.setup_database (Python; no psql/createdb needed) -------------------------


class _Cursor:
    def __init__(self, row=None, rows=()):
        self.row, self.rows = row, list(rows)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _FakeServer:
    """Records every statement per database; ``exists`` says whether the catalog DB is."""

    def __init__(self, exists: bool) -> None:
        self.exists = exists
        self.log: list[tuple[str, str]] = []

    def connect(self, **kw):
        server, dbname = self, kw["dbname"]

        class _Conn:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute(self, query, params=None):
                text = query if isinstance(query, str) else query.as_string(None)
                server.log.append((dbname, text))
                if "from pg_database" in text:
                    return _Cursor((1,) if server.exists else None)
                if "from pg_extension" in text:
                    return _Cursor(rows=[("pg_trgm", "1.6"), ("vector", "0.8.0")])
                return _Cursor()

        return _Conn()


@pytest.mark.parametrize("exists", [False, True])
def test_setup_database_creates_db_and_extensions_idempotently(isolated_env, monkeypatch, exists):
    server = _FakeServer(exists)
    monkeypatch.setattr(pg.psycopg, "connect", server.connect)
    line = pg.setup_database(Settings.from_env())
    assert ("created" if not exists else "exists") in line
    assert "vector 0.8.0" in line and "pg_trgm 1.6" in line
    statements = [t for _, t in server.log]
    assert any(t == 'create database "autoontology"' for t in statements) is (not exists)
    # create database runs on the maintenance DB, extensions in the catalog DB
    assert all(db == "postgres" for db, t in server.log if "database" in t)
    exts = [t for db, t in server.log if db == "autoontology" and t.startswith("create extension")]
    assert exts == [
        'create extension if not exists "vector"',
        'create extension if not exists "pg_trgm"',
    ]


def test_setup_database_treats_a_concurrent_create_as_exists(isolated_env, monkeypatch):
    """Review M-2: check-then-create races with a parallel setup or compose's first boot."""
    server = _FakeServer(False)
    real = server.connect

    def connect(**kw):
        conn = real(**kw)
        execute = conn.execute

        def racing(query, params=None):
            text = query if isinstance(query, str) else query.as_string(None)
            if text.startswith("create database"):
                raise pg.psycopg.errors.DuplicateDatabase('database "autoontology" already exists')
            return execute(query, params)

        conn.execute = racing
        return conn

    monkeypatch.setattr(pg.psycopg, "connect", connect)
    line = pg.setup_database(Settings.from_env())
    assert "(exists)" in line and "vector 0.8.0" in line


def test_setup_database_refuses_foreign_names_before_connecting(isolated_env, monkeypatch):
    called = []
    monkeypatch.setattr(pg.psycopg, "connect", lambda **kw: called.append(kw))
    settings = Settings(home=isolated_env)
    object.__setattr__(settings, "postgres_database", "moneybook")  # bypass __post_init__
    with pytest.raises(ValueError, match="moneybook"):
        pg.setup_database(settings)
    assert called == []


def test_setup_database_reports_connection_errors(isolated_env, monkeypatch):
    def refuse(**kw):
        raise pg.psycopg.OperationalError("connection refused")

    monkeypatch.setattr(pg.psycopg, "connect", refuse)
    with pytest.raises(pg.SetupError, match="cannot prepare database autoontology"):
        pg.setup_database(Settings.from_env())


def test_settings_read_postgres_values_like_python_dotenv(isolated_env, monkeypatch):
    env_file = isolated_env / ".env"
    env_file.write_text(DOTENV_CASES)
    monkeypatch.setenv("AUTOONTOLOGY_ENV_FILE", str(env_file))
    want = dotenv_values(env_file)
    s = Settings.from_env()
    assert (
        s.postgres_host,
        str(s.postgres_port),
        s.postgres_user,
        s.postgres_password,
        s.postgres_database,
    ) == (
        want["POSTGRES_HOST"],
        want["POSTGRES_PORT"],
        want["POSTGRES_USER"],
        want["POSTGRES_PASSWORD"],
        want["POSTGRES_DATABASE"],
    )


DOTENV_CASES = """\
# comment line
export POSTGRES_HOST=db.local   # inline comment
POSTGRES_PORT = 5433
POSTGRES_USER="quoted user" # trailing
POSTGRES_PASSWORD=p#ss  # only whitespace-prefixed # starts a comment
export POSTGRES_DATABASE='autoontology_src_crm' # quoted
"""


def test_cli_reports_bad_database_name_without_traceback(isolated_env, monkeypatch):
    from typer.testing import CliRunner

    from autoontology.cli import app

    monkeypatch.setenv("POSTGRES_DATABASE", "moneybook")
    result = CliRunner().invoke(app, ["status"])
    assert result.exit_code == 2
    assert "moneybook" in result.output and result.exception.__class__ is SystemExit


def test_overlong_database_and_source_names_are_refused():
    """Postgres truncates identifiers to 63 bytes, so two names sharing a 63-byte prefix
    would address the same database; the longer one must never validate."""
    from autoontology.config import SOURCE_NAME_MAX_LEN
    from autoontology.structured.pipeline import SourceError, pg_database_for, validate_source_name

    assert SOURCE_NAME_MAX_LEN == 63 - len("autoontology_src_") == 46
    a = "a" * SOURCE_NAME_MAX_LEN
    assert validate_source_name(a) == a
    assert len(pg_database_for(a)) == 63
    with pytest.raises(SourceError, match=f"at most {SOURCE_NAME_MAX_LEN}"):
        validate_source_name(a + "b")
    with pytest.raises(ValueError, match="63"):
        validate_pg_database_name("autoontology_src_" + a + "b")


def test_remove_revalidates_before_drop(isolated_env, monkeypatch):
    """A store row carrying an overlong pg_database (registered before the limit) is never
    passed to DROP DATABASE."""
    from autoontology.store.db import Store
    from autoontology.structured import remove

    settings = Settings.from_env()
    store = Store.for_home(settings.home)
    src = store.add_source(
        name="x", kind="files", locator="{}", pg_database="autoontology_src_" + "a" * 50
    )
    executed = []

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, *a):
            executed.append(sql)

    monkeypatch.setattr(pg, "connect", lambda *a, **k: _Conn())

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def delete_connection(self, db):
            pass

        def database_id_by_name(self, db):
            return None

    import autoontology.engine.client as client_mod

    monkeypatch.setattr(client_mod, "BackendClient", _Client)
    with pytest.raises(remove.RemoveError, match="63"):
        remove.remove_source(settings, store, src, drop_db=True)
    assert executed == []
    assert store.get_source("x") is not None

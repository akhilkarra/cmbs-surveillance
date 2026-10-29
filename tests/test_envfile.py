"""The local `.env` file read when the `cmbs` command starts."""

from pathlib import Path

from cmbs import envfile


def test_parse_reads_names_values_quotes_and_exports():
    text = (
        "# a comment\n"
        "\n"
        "DATABASE_URL=postgresql+psycopg://cmbs:cmbs@localhost:5432/cmbs\n"
        'SEC_USER_AGENT="Example Name name@example.com"\n'
        "export TYPESAFE_API_KEY='abc=def'\n"
        "EMPTY=\n"
        "  SPACED = value with spaces  \n"
        "no equals sign here\n"
        "=no name\n"
        'HALF="quote\n'
    )
    assert envfile.parse(text) == {
        "DATABASE_URL": "postgresql+psycopg://cmbs:cmbs@localhost:5432/cmbs",
        "SEC_USER_AGENT": "Example Name name@example.com",
        "TYPESAFE_API_KEY": "abc=def",
        "EMPTY": "",
        "SPACED": "value with spaces",
        "HALF": '"quote',
    }


def test_a_single_quote_character_is_kept():
    assert envfile.parse('ONE="\n') == {"ONE": '"'}


def test_load_sets_only_what_the_environment_lacks(tmp_path: Path):
    path = tmp_path / ".env"
    path.write_text("A=from file\nB=from file\n")
    environ = {"A": "from shell"}
    assert envfile.load(path, environ) == ["B"]
    assert environ == {"A": "from shell", "B": "from file"}


def test_a_missing_file_sets_nothing(tmp_path: Path):
    environ: dict[str, str] = {}
    assert envfile.load(tmp_path / ".env", environ) == []
    assert environ == {}

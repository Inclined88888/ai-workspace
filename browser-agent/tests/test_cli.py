import json

from browser_agent import cli


def test_parser_accepts_flags_after_subcommand():
    args = cli.build_parser().parse_args(["open", "example.com", "--json", "--headed"])
    assert args.cmd == "open" and args.json and args.headed


def test_open_json(site, capsys):
    assert cli.main(["open", f"{site}/index.html", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Test Shop"


def test_providers_lists_all(capsys):
    assert cli.main(["providers", "--json"]) == 0
    names = {r["provider"] for r in json.loads(capsys.readouterr().out)}
    assert {"anthropic", "gemini", "ollama", "openai"} <= names


def test_errors_are_one_line(capsys):
    assert cli.main(["click", "http://127.0.0.1:9/", "#x"]) == 1
    assert capsys.readouterr().err.startswith("error:")

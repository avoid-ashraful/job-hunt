import io
import zipfile
from pathlib import Path

import pytest
import yaml

from scanner import seed_sources as seeds

FIXTURES = Path(__file__).parent / "fixtures"


def archive(text):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as files:
        files.writestr("remote-jobs-main/src/companies/product.md", text)
        files.writestr("remote-jobs-main/README.md", text)
    return stream.getvalue()


def test_awesome_explicit_hiring_and_quality():
    result = seeds.parse_awesome((FIXTURES / "seed_awesome.md").read_text())
    assert [s["company"] for s in result] == ["Ergeon", "Iterative"]
    assert result[0]["careers_url"] == "https://ergeon.example/careers/"
    assert all(s["hires"] == "worldwide" and s["source"] == "awesome-remote-job" for s in result)


def test_regional_company_metadata():
    result = seeds.parse_remoteintech(archive((FIXTURES / "seed_remoteintech.md").read_text()))
    assert len(result) == 1
    assert result[0]["company"] == "Regional Product"
    assert result[0]["hq_country"] == "sg"
    assert result[0]["website"] == "https://product.example"
    assert "North America, APAC, Europe" in result[0]["why"]


@pytest.mark.parametrize("region", ["India", "Indonesia, South East Asia", "Europe", "Asia: Singapore, Japan",
                                   "Asia\n### Region Details\nSingapore, Japan", "Asia\nWe hire only in India.", "Asia, excluding Bangladesh"])
def test_regional_rejects_narrow_or_missing_eligibility(region):
    text = (FIXTURES / "seed_remoteintech.md").read_text().replace("North America, APAC, Europe", region)
    assert not seeds.parse_remoteintech(archive(text))


@pytest.mark.parametrize("before,after", [
    ("region: other", "region: worldwide"),
    ("region: other", "region: other\nhq_country: bd"),
    ("headquartered in Singapore", "headquartered in India"),
    ("database product", "staffing agency"),
    ("database product", "talent marketplace"),
    ("database product", "software consultancy"),
    ("## Office locations\nSingapore", "## Office locations\nDhaka, Bangladesh"),
])
def test_regional_exclusions(before, after):
    text = (FIXTURES / "seed_remoteintech.md").read_text().replace(before, after)
    assert not seeds.parse_remoteintech(archive(text))


def test_configured_company_rules_and_hq():
    text = (FIXTURES / "seed_remoteintech.md").read_text()
    for filters in [{"exclude": {"company_name_keywords": ["regional"]}},
                    {"company_market": {"hq_overrides": {"regional-product": "in"}}}]:
        assert not seeds.parse_remoteintech(archive(text), {"filters": filters})


def test_merge_preserves_existing_fields_and_counts_only_additions():
    hand = [{"company": "Acme Inc.", "ats": "ashby", "board": "custom", "aliases": ["AC"]}]
    existing = [{"company": "Old", "source": "old", "why": "keep me"}]
    incoming = [{"company": "Acme LLC", "source": "a"}, {"company": "Old Inc.", "source": "a"},
                {"company": "New", "source": "a"}, {"company": "New Ltd.", "source": "b"}]
    merged, counts = seeds.merge_seeds(existing, incoming, hand)
    assert merged == [incoming[2], existing[0]]
    assert counts == {"a": 1}
    assert hand[0]["board"] == "custom"


def test_update_is_idempotent_and_leaves_hand_file_untouched(tmp_path, monkeypatch):
    hand, auto = tmp_path / "hand.yml", tmp_path / "auto.yml"
    original = "# Preserve this comment\ncompanies:\n- company: Acme\n  board: acme-custom\n"
    hand.write_text(original)
    monkeypatch.setattr(seeds, "fetch_awesome", lambda cfg: [{"company": "ACME Inc.", "source": "a"},
                                                            {"company": "New", "source": "a"}])
    monkeypatch.setattr(seeds, "fetch_remoteintech", lambda cfg: [{"company": "New LLC", "source": "b"}])
    assert seeds.update_seeds(hand, auto, {}) == {"added": {"a": 1}, "total": 1}
    first = auto.read_bytes()
    assert seeds.update_seeds(hand, auto, {}) == {"added": {}, "total": 1}
    assert auto.read_bytes() == first
    assert hand.read_text() == original
    with seeds.build_seeds(hand, auto) as combined:
        rows = yaml.safe_load(combined.read_text())["companies"]
        assert rows == [{"company": "Acme", "board": "acme-custom"}, {"company": "New", "source": "a"}]
    assert not combined.exists()


def test_build_seeds_missing_files(tmp_path):
    with seeds.build_seeds(tmp_path / "hand", tmp_path / "auto") as combined:
        assert seeds.read_seeds(combined) == []


def test_fetchers_use_their_public_endpoints(monkeypatch):
    class Response:
        text = (FIXTURES / "seed_awesome.md").read_text()
        content = archive((FIXTURES / "seed_remoteintech.md").read_text())

    calls = []
    monkeypatch.setattr(seeds, "_get", lambda url: calls.append(url) or Response())
    assert len(seeds.fetch_awesome()) == 2
    assert len(seeds.fetch_remoteintech()) == 1
    assert calls == [seeds.AWESOME_URL, seeds.REMOTEINTECH_URL]


def test_cli_build_combines_both_files(tmp_path, monkeypatch):
    from scanner import cli

    config = tmp_path / "config.yml"
    config.write_text("candidate: {}\noutput:\n  state_dir: state\n")
    (tmp_path / "watchlist.seeds.yml").write_text("companies:\n- company: Hand\n  ats: ashby\n  board: special\n")
    (tmp_path / "watchlist.seeds.auto.yml").write_text("companies:\n- company: Auto\n")
    captured = []
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "_hq_lookup", lambda *args: None)
    monkeypatch.setattr(cli.watchlist, "build", lambda *args, **kwargs: captured.extend(seeds.read_seeds(kwargs["seeds_file"])))
    monkeypatch.setattr("sys.argv", ["scanner", "--config", str(config), "watchlist", "build"])
    cli.main()
    assert captured == [{"company": "Auto"}, {"company": "Hand", "ats": "ashby", "board": "special"}]


def test_cli_seeds_runs_importer(tmp_path, monkeypatch):
    from scanner import cli

    config = tmp_path / "config.yml"
    config.write_text("filters: {}\n")
    captured = []
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(seeds, "update_seeds", lambda *args: captured.append(args))
    monkeypatch.setattr("sys.argv", ["scanner", "--config", str(config), "watchlist", "seeds"])
    cli.main()
    assert captured == [(tmp_path / "watchlist.seeds.yml", tmp_path / "watchlist.seeds.auto.yml", {"filters": {}})]


def test_failed_fetch_leaves_auto_file_untouched(tmp_path, monkeypatch):
    import requests

    auto = tmp_path / "auto.yml"
    auto.write_text("companies: []\n")
    monkeypatch.setattr(seeds, "fetch_awesome", lambda cfg: [{"company": "New", "source": "a"}])

    def fail(cfg):
        raise requests.HTTPError("429")

    monkeypatch.setattr(seeds, "fetch_remoteintech", fail)
    with pytest.raises(requests.HTTPError):
        seeds.update_seeds(tmp_path / "hand.yml", auto, {})
    assert auto.read_text() == "companies: []\n"

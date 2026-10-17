"""Anonymized shapes from the October 17 live board overlap audit."""

from dataclasses import replace

import pytest

from scanner.cli import _fill_missing
from scanner.dedup import merge_jobs
from scanner.models import Job
from scanner.sources._boards import board_job
from scanner.store import Store


def employer(**fields):
    job = Job(id="ashby:acme:123", source="ats", url="https://jobs.ashbyhq.com/acme/123",
              company="Acme", company_key="acme", title="Senior Software Engineer II, Applied AI",
              description="Employer's full description", watchlist=True)
    return replace(job, **fields)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("identity", ["id", "url", "title"])
def test_employer_wins_and_borrows_board_enrichment(reverse, identity):
    live = employer()
    board = replace(live, id="remoteok::42", source="remoteok", url="https://remoteok.com/42",
                    title="Senior Software Engineer II Applied AI (Remote)",
                    company="Acme Inc.", company_key="acme-inc", description="Short board text",
                    salary_min=120000, salary_currency="USD", skills=["python"], watchlist=False)
    if identity == "id":
        board.id, board.title = live.id, "A rewritten title"
    elif identity == "url":
        board.url, board.title = live.url + "?utm_source=board", "A rewritten title"
    jobs = [board, live] if reverse else [live, board]
    assert list(merge_jobs(jobs, _fill_missing).values()) == [live]
    assert live.description == "Employer's full description" and live.watchlist
    assert live.salary_min == 120000 and live.skills == ["python"]
    assert live.id == "ashby:acme:123" and live.source == "ats"


def client_copy():
    text = "Qué es Brando\n" + " ".join(f"distinctive{i}" for i in range(100))
    original = employer(id="himalayas::123", source="himalayas", title="Sr REACT NATIVE (Brando)",
                        description=text)
    board = replace(original, id="remoteok::42", source="remoteok", title="Sr REACT NATIVE",
                    url="https://remoteok.com/42", description=text.encode("utf-8").decode("latin1"))
    return original, board


def test_client_suffix_requires_matching_description():
    original, board = client_copy()
    assert list(merge_jobs([board, original], _fill_missing).values()) == [original]
    unrelated = replace(board, description="A different project. " * 100)
    assert len(merge_jobs([original, unrelated], _fill_missing)) == 2


@pytest.mark.parametrize("level", ["Senior", "Staff", "Principal", "Junior", "II"])
def test_description_match_keeps_seniority_and_level(level):
    text = f"{level} engineers wanted\n" + "distinctive words " * 100
    original = employer(title=f"Backend Engineer ({level})", description=text)
    board = replace(original, id="jobicy::42", source="jobicy", url="https://jobicy.com/42",
                    title="Backend Engineer")
    assert len(merge_jobs([original, board], _fill_missing)) == 2


@pytest.mark.parametrize("reverse", [False, True])
def test_historical_client_suffix_matches_without_rewriting_private_state(tmp_path, reverse):
    original, board = client_copy()
    if reverse:
        original, board = board, original
    store = Store(tmp_path / "state.sqlite")
    store.upsert(original, "2026-10-17")
    store.mark_reported(original, "2026-10-17")
    assert "same company+title variant+text" in store.already_reported(board)
    store.db.close()


def test_bridge_copy_merges_all_aliases():
    live = employer()
    other = employer(id="freehire::other", source="freehire", title="Backend Engineer",
                     url="https://example.com/other", salary_min=100000)
    bridge = replace(other, id="remoteok::bridge", source="remoteok", url=live.url, skills=["python"])
    alias = replace(other, id="jobicy::alias", source="jobicy", salary_max=150000)
    assert list(merge_jobs([other, bridge, live, alias], _fill_missing).values()) == [live]
    assert (live.salary_min, live.salary_max, live.skills) == (100000, 150000, ["python"])


def test_board_employer_apply_link_uses_live_checkable_id():
    job = board_job(source="remoteok", key="42", source_url="https://remoteok.com/42",
                    url="https://jobs.ashbyhq.com/acme/abc-123?utm_source=board",
                    company="Acme Inc.", title="AI Platform Engineer", description="Python",
                    location="Worldwide", posted=None)
    assert job.id == "ashby:acme:abc-123"
    assert job.url == "https://jobs.ashbyhq.com/acme/abc-123"
    assert job.ats_metadata["source_url"] == "https://remoteok.com/42"


def test_same_text_under_tenant_and_brand_names_still_merges():
    live = employer(description=" ".join(f"distinctive{i}" for i in range(100)))
    board = replace(live, id="jobicy::42", source="jobicy", company="Parent Brand",
                    company_key="parent-brand", url="https://jobicy.com/42", salary_min=120000)
    assert list(merge_jobs([board, live], _fill_missing).values()) == [live]
    assert live.salary_min == 120000

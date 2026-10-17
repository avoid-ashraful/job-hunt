from collections import Counter
from dataclasses import replace
from datetime import datetime

import pytest

from scanner.marks import parse
from scanner.models import Job
from scanner.report import check_score, write_report
from scanner.store import job_ref


def job(index=0, **kwargs):
    defaults = dict(id=f"test:{index}", source="freehire", url=f"https://example.com/{index}",
                    title="Python Engineer", company=f"Company {index}", company_key=f"company{index}",
                    description="Python", bucket="check",
                    screen={"fit": 3, "hq_country": "us", "why": "description says nothing about location"})
    defaults.update(kwargs)
    return Job(**defaults)


def render(path, jobs, **kwargs):
    write_report(path, datetime(2026, 10, 17), jobs,
                 {"fetched": len(jobs), "passed": len(jobs), "ambiguous": 0, "llm_calls": 0},
                 Counter(), "Bangladesh", **kwargs)
    return path.read_text()


def test_check_score_evidence_ordering():
    base = job()
    stronger = [replace(base, watchlist=True),
                replace(base, url="https://job-boards.greenhouse.io/acme/jobs/123"),
                replace(base, source="ats"),
                replace(base, screen={**base.screen, "eligibility": "includes_candidate"}),
                replace(base, screen={**base.screen, "decided_by": "positive.worldwide"}),
                replace(base, screen={**base.screen, "preferred_hq": True}),
                replace(base, screen={**base.screen, "eligibility": "timezone_only"}),
                replace(base, screen={**base.screen, "fit": 5}),
                replace(base, screen={**base.screen, "pay_hint": "$100k"}),
                replace(base, description="Python. We hire anywhere via Deel."),
                replace(base, description="Python. You will join as an independent contractor.")]
    assert [check_score(j) - check_score(base) for j in stronger] == [8, 4, 4, 6, 6, 4, 3, 2, 2, 4, 4]
    unknown = replace(base, screen={**base.screen, "hq_country": ""})
    ghost = replace(base, screen={**base.screen, "ghost": (2, ["scraped_repost"])})
    aggregator = replace(base, screen={**base.screen, "ghost": (2, ["aggregator_source (whatjobs)"])})
    assert check_score(base) > check_score(unknown) > check_score(ghost) > check_score(aggregator)
    assert check_score(job(screen={})) == -3
    # A source's worldwide tag is not confirmation from the posting's text.
    assert check_score(replace(base, location="Remote, Worldwide", regions=["global"])) == check_score(base)


@pytest.mark.parametrize("limit,expanded", [(None, 25), (2, 2), (0, 0), (-1, 0), (40, 30)])
def test_check_top_split_and_start_here(tmp_path, limit, expanded):
    jobs = [job(i) for i in range(30)]
    jobs[-1].watchlist = True
    path = tmp_path / "jobs.md"
    text = render(path, jobs, **({} if limit is None else {"check_top": limit}))
    start, check = text.split("## 🟡 Check (30)")
    assert start.index(job_ref(jobs[-1].id)) < start.index(job_ref(jobs[0].id))
    main = check.split("<details>", 1)[0]
    assert main.count("| [ ] |") == expanded
    if expanded:
        assert main.index(job_ref(jobs[-1].id)) < main.index(job_ref(jobs[0].id))
    if expanded < 30:
        folded = check.split("<details>", 1)[1].split("</details>", 1)[0]
        assert f"More Check jobs ({30 - expanded})" in folded
        assert folded.count("| [ ] |") == 30 - expanded
    else:
        assert "More Check jobs" not in text
    assert len(parse(path)) == 30


def test_folded_check_groups_keep_marks_and_start_here_precedence(tmp_path):
    jobs = [job(i, screen={"fit": 2, "why": f"eligible, but low stack fit (stack matches: {i})"})
            for i in range(2)]
    jobs.append(job(2, screen={"fit": 1, "why": "AI couldn't tell from the text"}))
    path = tmp_path / "jobs.md"
    text = render(path, jobs, check_top=0, start_here_limit=1)
    assert "### eligible, but low stack fit (2)" in text
    assert "### AI couldn't tell from the text (1)" in text
    start, folded = text.split("<details>", 1)
    first, second, third = [job_ref(j.id) for j in jobs]
    start = start.replace(f"`{first}` | [ ]", f"`{first}` | [a sent]")
    folded = folded.replace(f"`{first}` | [ ]", f"`{first}` | [x US only]")
    folded = folded.replace(f"`{second}` | [ ]", f"`{second}` | [g investigate]")
    path.write_text(start + "<details>" + folded)
    assert parse(path) == {first: ("applied", "sent"), second: ("good", "investigate"), third: None}

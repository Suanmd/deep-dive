"""Tests for query classification."""

from __future__ import annotations

from deep_dive.query_classifier import all_medium_alternatives


class TestMediumAlternatives:
    def test_returns_tuple(self):
        alts = all_medium_alternatives()
        assert isinstance(alts, tuple)
        assert "substack.com" in alts
        assert "dev.to" in alts


class TestPickSiteQuery:
    """Per-site English term matching.

    Previously every site-targeted task used
    ``english_search_terms[0]`` regardless of the site, which wasted
    ``terms[1:]`` when ``target_sites`` had specialised vocabulary. For
    ``huggingface.co`` the baseline ``"LLM leaderboard Chatbot Arena
    LMSYS"`` returns 0 hits because HF isn't in that term;
    ``"Hugging Face Open LLM Leaderboard"`` does. Now
    :func:`pick_site_query` routes each site to its best-matching
    English term via substring overlap (no hardcoded alias table —
    the LLM plan stays the source of truth).
    """

    def test_picks_term_with_site_token_lmsys(self):
        from deep_dive.query_classifier import pick_site_query

        terms = (
            "LLM leaderboard 2026 Chatbot Arena LMSYS",
            "Open LLM Leaderboard Hugging Face 2026",
        )
        # Substring "lmsys" appears only in terms[0] → pick terms[0].
        assert pick_site_query("lmsys.org", terms, fallback="fb") == terms[0]

    def test_picks_term_with_site_token_huggingface(self):
        from deep_dive.query_classifier import pick_site_query

        terms = (
            "LLM leaderboard 2026 Chatbot Arena LMSYS",
            "Open LLM Leaderboard Hugging Face 2026",
        )
        # Substrings "hugg"/"face"/"hugging" of "huggingface" hit terms[1].
        assert pick_site_query("huggingface.co", terms, fallback="fb") == terms[1]

    def test_different_sites_get_different_terms(self):
        """Multiple sites → each picks its own best-matching term.

        This is the actual LLM-leaderboard failure case the user reported.
        Pre-fix, both sites got terms[0] (waste). Post-fix, each gets
        its own specialised term.
        """
        from deep_dive.query_classifier import pick_site_query

        terms = (
            "LLM leaderboard 2026 Chatbot Arena LMSYS",
            "Open LLM Leaderboard Hugging Face 2026",
            "arxiv cs.LG 2026 latest LLM papers",
        )
        lmsys_q = pick_site_query("lmsys.org", terms, fallback="fb")
        hf_q = pick_site_query("huggingface.co", terms, fallback="fb")
        arxiv_q = pick_site_query("arxiv.org", terms, fallback="fb")
        assert lmsys_q == terms[0]
        assert hf_q == terms[1]
        assert arxiv_q == terms[2]
        # And lmsys ≠ huggingface (pre-fix bug: both got terms[0]).
        assert lmsys_q != hf_q

    def test_falls_back_when_no_substring_overlap(self):
        from deep_dive.query_classifier import pick_site_query

        terms = ("LLM leaderboard 2026 Chatbot Arena",)
        # zhihu.com → no 4+ char substring matches "LLM leaderboard…"
        assert pick_site_query("zhihu.com", terms, fallback="中文 知乎") == "中文 知乎"

    def test_falls_back_when_terms_empty(self):
        from deep_dive.query_classifier import pick_site_query

        assert pick_site_query("lmsys.org", [], fallback="default") == "default"

    def test_falls_back_when_terms_is_none(self):
        from deep_dive.query_classifier import pick_site_query

        assert pick_site_query("lmsys.org", None, fallback="default") == "default"

    def test_strips_www_subdomain(self):
        from deep_dive.query_classifier import pick_site_query

        terms = ("LLM leaderboard 2026 Chatbot Arena LMSYS", "other")
        # www.lmsys.org and lmsys.org should pick the same term.
        r1 = pick_site_query("www.lmsys.org", terms, fallback="fb")
        r2 = pick_site_query("lmsys.org", terms, fallback="fb")
        assert r1 == r2 == terms[0]

    def test_strips_docs_subdomain(self):
        from deep_dive.query_classifier import pick_site_query

        terms = ("Python tutorial docs reference", "Java guide")
        # docs.python.org → token "python" matches terms[0]
        assert pick_site_query("docs.python.org", terms, fallback="fb") == terms[0]

    def test_case_insensitive(self):
        from deep_dive.query_classifier import pick_site_query

        terms = ("Hugging Face Open LLM Leaderboard",)
        # Mixed case site still matches.
        assert pick_site_query("HUGGINGFACE.CO", terms, fallback="fb") == terms[0]

    def test_tie_breaks_to_earliest_index(self):
        from deep_dive.query_classifier import pick_site_query

        # Both terms mention "lmsys" equally — earliest index wins.
        terms = ("abc lmsys test", "xyz lmsys other")
        assert pick_site_query("lmsys.org", terms, fallback="fb") == terms[0]

    def test_longer_substring_higher_score(self):
        from deep_dive.query_classifier import pick_site_query

        # terms[1] contains BOTH "arxiv" AND "papers" while terms[0]
        # only contains "papers"; terms[1] should win for arxiv.org.
        terms = ("papers 2026", "arxiv papers 2026")
        assert pick_site_query("arxiv.org", terms, fallback="fb") == terms[1]

    def test_github_route_to_github_term(self):
        from deep_dive.query_classifier import pick_site_query

        terms = (
            "LLM leaderboard 2026 Chatbot Arena LMSYS",
            "Open LLM Leaderboard Hugging Face 2026",
            "GitHub trending LLM repositories 2026",
        )
        # "github" substrings hit terms[2] (contains "github").
        assert pick_site_query("github.com", terms, fallback="fb") == terms[2]

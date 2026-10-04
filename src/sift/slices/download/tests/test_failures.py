# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reading a tool's output for what actually went wrong.

The order is the design and so it is what these hold to: a site's own refusal beats a condition
every site shares, and both beat a status code. The other half of it is the refusal to guess: an
output nothing recognises produces nothing, and the caller keeps its own plain sentence rather than
being handed a specific explanation that happens to be wrong.
"""

from __future__ import annotations

import pytest

from sift.kernel.access import sentences
from sift.slices.download.sources import failures
from sift.slices.download.sources.sites.catalog import SITES, Meaning

_YOUTUBE = "https://www.youtube.com/watch?v=abc"
_UNKNOWN = "https://a-site-sift-has-never-heard-of.example/x"
#: A file host: recognised, fetched, and with no account anywhere in it to be signed in to.
_A_FILE_HOST = "https://gofile.io/d/some-album"


def test_a_status_code_gets_its_standard_phrase_and_nothing_hand_written() -> None:
    """The phrase is the standard library's. What a 404 MEANS for a download is written once, in
    `STATUS_MEANS`, and is the second half of the sentence: the first half is never hand-written."""
    found = failures.classify(_UNKNOWN, "ERROR: HTTP Error 404: Not Found")
    assert found is not None
    assert found.tier == 2
    assert found.code == "http-404"
    assert "answered 404 Not Found: " in found.sentence
    assert found.said == "404 Not Found"


def test_a_status_with_no_meaning_written_is_the_code_alone_at_tier_one() -> None:
    found = failures.classify(_UNKNOWN, "ERROR: HTTP Error 400: Bad Request")
    assert found is not None
    assert found.tier == 1
    assert found.sentence.endswith("answered 400 Bad Request.")


def test_being_signed_out_and_being_refused_stay_different() -> None:
    """401 and 403 send somebody to two different places, so they are never collapsed."""
    unauthorized = failures.classify(_UNKNOWN, "HTTP Error 401: Unauthorized")
    forbidden = failures.classify(_UNKNOWN, "HTTP Error 403: Forbidden")
    assert unauthorized is not None and forbidden is not None
    assert unauthorized.code != forbidden.code


def test_a_code_no_standard_names_is_explained_and_shown() -> None:
    """The number is in the sentence: the code is drawn nowhere else a person reads, and a row
    that hides the code leaves somebody guessing. The words stay; the number leads them."""
    found = failures.classify(_UNKNOWN, "HTTP Error 522: ")
    assert found is not None
    assert found.tier == 2
    assert "answered 522: the Site behind the network did not answer in time" in found.sentence


def test_a_condition_that_is_not_a_status_at_all_is_recognised() -> None:
    found = failures.classify(_UNKNOWN, "OSError: [Errno 28] No space left on device")
    assert found is not None
    assert found.tier == 2
    assert found.code == "no-space"
    assert "Free some space" in found.sentence


def test_a_sites_own_wall_beats_a_status_code_that_came_with_it() -> None:
    """The whole reason tier 3 exists: the status is a 403 and says nothing, and the words do."""
    found = failures.classify(
        _YOUTUBE, "ERROR: HTTP Error 403: Forbidden. Sign in to confirm your age."
    )
    assert found is not None
    assert found.tier == 3
    # The site's own words, not "The site answered 403 Forbidden.", which is what a 403 alone
    # produces, and what this would return if the phrase matched no wall.
    assert found.code == "wall-age"
    assert "403" not in found.sentence


def test_an_age_check_is_named_as_one_rather_than_as_a_403() -> None:
    """An age gate answers 403 like everything else, so without this it is filed as "the site said
    no" and the one thing that would clear it (a saved login, or a tunnel to a country that lifts
    it) is never mentioned."""
    found = failures.classify(_YOUTUBE, "ERROR: Sign in to confirm your age. This video may be...")
    assert found is not None
    assert found.code == "wall-age"
    assert "cookies" in found.sentence.lower()


def test_a_walls_code_is_declared_rather_than_read_off_its_sentence() -> None:
    """The code is what a row prints and what a search matches on. Derived from the sentence it
    would change whenever the copy was edited, and say nothing: the login wall's first three words
    are "The site would"."""
    from sift.slices.download.sources.sites.catalog import SITES

    found = failures.classify("https://www.instagram.com/p/x/", "ERROR: Login required")
    assert found is not None
    assert found.code == "wall-login"
    assert {wall.kind for site in SITES for wall in site.walls} == {"age", "login", "region"}


def test_a_refusal_about_where_the_request_came_from_offers_the_tunnel() -> None:
    """A location block is the one failure the other half of this slice can fix, so it must reach
    `jobs.py`, which is what turns the flag into a sentence naming the remedy.

    Asserted on the status codes rather than on a site's own wall. A region block comes back as one
    of these whatever the site, and no wall carries the flag: a site's wall that is about location
    says so in its own sentence, and a second appended one would be the same advice twice."""
    for status in (403, 451):
        found = failures.classify("https://gofile.io/d/x", f"HTTP Error {status}: nope")
        assert found is not None, status
        assert found.a_tunnel_would_help is True, status
    # And not for a refusal that has nothing to do with where the request came from.
    ordinary = failures.classify("https://gofile.io/d/x", "HTTP Error 404: Not Found")
    assert ordinary is not None
    assert ordinary.a_tunnel_would_help is False


def test_a_wall_about_a_missing_login_does_not_offer_a_tunnel() -> None:
    """A tunnel does not sign anybody in, and suggesting one there sends somebody the wrong way."""
    found = failures.classify("https://www.instagram.com/p/x/", "ERROR: Login required")
    assert found is not None
    assert found.tier == 3
    assert found.a_tunnel_would_help is False


def test_a_site_with_no_walls_of_its_own_still_gets_the_lower_tiers() -> None:
    """Most sites have no phrases written for them, and that must not mean no explanation."""
    found = failures.classify("https://gofile.io/d/x", "HTTP Error 429: Too Many Requests")
    assert found is not None
    assert found.tier == 2
    assert found.code == "http-429"
    assert found.sentence == (
        "GoFile answered 429 Too Many Requests: the Site is limiting how many requests it "
        "accepts; try again in a few minutes."
    )
    assert not found.final


def test_an_output_nothing_recognises_produces_nothing() -> None:
    """Saying nothing is a real answer. A confident wrong explanation is worse than a vague one."""
    assert failures.classify(_UNKNOWN, "Traceback (most recent call last): ValueError") is None


def test_an_unassigned_status_number_does_not_pretend_to_name_itself() -> None:
    found = failures.classify(_UNKNOWN, "HTTP Error 418: ")
    assert found is not None
    assert "418" in found.sentence


def test_detailed_logging_does_not_stop_a_failure_being_recognised() -> None:
    """The one interaction worth pinning between two features that look unrelated.

    Turning on detailed logging asks both tools to explain every step, and every one of those lines
    lands on the same stream a failure is read from. If a reading depended on the output being short
    or on a line's position, switching that setting on would quietly make every failure generic,
    which is the opposite of what somebody turning it on is trying to achieve. Nothing here reads
    position or length, and this is what says so.
    """
    quiet = "ERROR: HTTP Error 403: Forbidden. This video is not available in your country."
    chatty = "\n".join(
        [
            "[debug] Command-line config: ['--verbose']",
            "[debug] Encodings: locale UTF-8, fs utf-8",
            "[debug] Loaded 1863 extractors",
            quiet,
            "[debug] Exiting with code 1",
        ]
    )
    assert failures.classify(_YOUTUBE, chatty) == failures.classify(_YOUTUBE, quiet)


def test_every_wall_on_every_site_is_a_pattern_that_compiles() -> None:
    """A wall is a regular expression written by hand, and a broken one would only be discovered
    the next time that site refused a download, which is the worst moment to find out."""
    import re

    from sift.slices.download.sources.sites.catalog import SITES

    for site in SITES:
        for wall in site.walls:
            re.compile(wall.pattern)
            assert wall.sentence.strip()
            # Tier 3 exists to say what to DO. A real sentence, then (not a bare status word),
            # but not a minimum paragraph either: "Cookies needed." is the whole of the remedy on
            # four of these sites and a longer way of saying it helps nobody.
            assert len(wall.sentence.split()) >= 2, wall.sentence
            assert wall.sentence.rstrip().endswith("."), wall.sentence


def test_a_status_is_recognised_however_the_tool_phrased_it() -> None:
    """The two tools say it differently, and a reading that only knew one of them would silently
    stop explaining half the failures the moment either tool reworded itself."""
    for output in (
        "ERROR: HTTP Error 503: Service Unavailable",
        "requests.exceptions.HTTPError: 503 Server Error: Service Unavailable",
        "giving up: status=503",
    ):
        found = failures.classify(_UNKNOWN, output)
        assert found is not None, output
        assert found.code == "http-503"


def test_the_adult_interstitial_is_read_as_the_block_it_is_on_this_site() -> None:
    """The door the large adult sites put in front of everything, seen on the site itself.

    It is a PAGE, not a status: the request succeeds and what comes back is "This is an adult
    website" with a button on it. Nothing about the response says so, which is the whole reason a
    site's own words are read.

    The kind is the site's, not the page's: the same words are an age gate on one site and a
    location block on this one, and the kind is what a row and a search match on. The words are
    the age gate's; the kind is the site's.
    """
    found = failures.classify(
        "https://www.pornhub.com/view_video.php?viewkey=abc",
        "ERROR: unable to download webpage: This is an adult website. I am 18 or older - Enter",
    )
    assert found is not None
    assert found.code == "wall-region"
    assert found.tier == 3
    assert not found.cookies_would_help


def test_being_sent_to_the_front_page_is_read_as_a_block_on_where_you_are() -> None:
    """A 302 to the site's own home page, reported by the tool as a redirect.

    Seen from an ordinary connection: nothing in it says "you are not welcome here", so without this
    it lands as a generic failure and the one thing that gets past it (another exit) is never
    mentioned.
    """
    found = failures.classify(
        "https://www.pornhub.com/view_video.php?viewkey=x",
        "ERROR: [PornHub] x: Redirection detected; the video may be deleted or require login",
    )
    assert found is not None
    assert found.code == "sent-to-the-front-page"
    assert found.a_tunnel_would_help is True


def test_a_condition_that_a_tunnel_cannot_fix_does_not_offer_one() -> None:
    """The flag is per condition, not per tier. A full disk is not answered by another exit."""
    found = failures.classify(_UNKNOWN, "OSError: [Errno 28] No space left on device")
    assert found is not None
    assert found.a_tunnel_would_help is False


def test_every_failure_sift_has_words_for_is_one_this_slice_can_write() -> None:
    """The sentence table upstairs is keyed on codes, and a key nothing writes is a line nobody sees.

    It is the kind of mistake that leaves no trace: the table reads as covering a case, the case
    happens, and the row falls through to the stored sentence exactly as if the line had never been
    written. So the keys are checked against the three shapes this slice actually produces (a
    site's own wall, one of the named conditions, `http-<status>`, and the codes the reader one layer
    up assigns itself) rather than against a list somebody remembers to keep.

    It runs the other way too, which is the half worth having: a condition or a wall added here
    without a thought for what the row says is left visible in the report below rather than
    asserted, because most of them SHOULD say nothing: their own sentence is better.
    """
    walls = {f"wall-{wall.kind}" for record in SITES for wall in record.walls}
    conditions = {code for code, _pattern, _sentence, _tunnel in failures._CONDITIONS}
    conditions |= {code for code, *_rest in failures._TOOL_REASONS}
    for code in sentences.DOWNLOAD_SAID:
        if code.startswith("http-"):
            assert code[len("http-") :].isdigit(), code
            continue
        assert code in walls or code in conditions or code in failures.INTERPRETED_CODES, code


def test_the_two_codes_whose_stored_words_say_login_are_answered() -> None:
    """The one thing this build MUST override rather than may. Both of these store a sentence that
    sends somebody to a screen called Connections to replace a login, and there is no login and no
    such screen: cookies exported from a browser are the whole of what Sift is ever given."""
    for code in ("wall-login", "login-failed"):
        said = sentences.download_failed(code, "Sunsetter")
        assert said is not None, code
        assert "login" not in said.lower()
        assert "cookies" in said


# --- whether cookies are the answer, which decides whether a row waits ------------------------


def test_a_refusal_cookies_get_past_says_so_on_a_site_they_help_on() -> None:
    """A YouTube 403 read as a plain failure would ask for cookies under a button that only offers
    Try again. What decides it is not in the output at all: it is the site's own record."""
    found = failures.classify(_YOUTUBE, "ERROR: HTTP Error 403: Forbidden")
    assert found is not None
    assert found.code == "http-403"
    assert found.cookies_would_help


def test_the_same_refusal_on_a_site_with_no_account_offers_nothing() -> None:
    """Both halves, and this is the half the code alone would get wrong: a 403 is a 403 everywhere,
    and a file host has nothing anybody could sign in to and add."""
    found = failures.classify(_A_FILE_HOST, "ERROR: HTTP Error 403: Forbidden")
    assert found is not None
    assert found.code == "http-403"
    assert not found.cookies_would_help


def test_a_site_sift_has_never_heard_of_offers_nothing_either() -> None:
    """No record, so nothing is known about whether cookies would help, and silence is the honest
    answer where a guess would park a download waiting for something that cannot arrive."""
    found = failures.classify(_UNKNOWN, "ERROR: HTTP Error 403: Forbidden")
    assert found is not None
    assert not found.cookies_would_help


def test_a_failure_cookies_cannot_touch_never_offers_them() -> None:
    """The other half the record alone would get wrong. A post that is gone is gone whoever asks."""
    found = failures.classify(_YOUTUBE, "ERROR: HTTP Error 404: Not Found")
    assert found is not None
    assert found.code == "http-404"
    assert not found.cookies_would_help


def test_every_code_cookies_answer_is_a_refusal_and_not_a_fault() -> None:
    """The set is small on purpose and this is what keeps it small: each member is a way of being
    told "you are nobody here", never a way of being told the site is busy or broken."""
    assert {"wall-login", "wall-age", "http-401", "http-403"} == failures.COOKIES_ANSWER


def test_an_age_gate_on_a_site_cookies_help_on_waits_for_them() -> None:
    """The age wall waits like the login wall, because the fix is the same jar one press away. The
    sentence written for it is the waiting row's reason."""
    found = failures.classify(_YOUTUBE, "ERROR: Sign in to confirm your age")
    assert found is not None
    assert found.code == "wall-age"
    assert found.cookies_would_help


# --- the lines the table draws for the codes the reader assigns ------------------------------


def test_the_two_walls_that_are_not_about_being_nobody_say_what_gets_past_them() -> None:
    """They want opposite things, and a row is read at a glance. An age gate lifts for a jar from a
    browser somebody is signed in to; a region block lifts for nothing anybody can add."""
    age = sentences.download_failed("wall-age", "Sunsetter")
    region = sentences.download_failed("wall-region", "Sunsetter")
    assert age is not None and "cookies" in age
    assert region is not None and "tunnel" in region
    assert "cookies" not in region


def test_a_site_nothing_here_can_read_is_not_reported_as_the_sites_fault() -> None:
    """The site is fine and the post may well be there. A line saying it refused would send
    somebody to fix something that is not broken."""
    assert (
        sentences.download_failed("unsupported", "Sunsetter")
        == "Sift cannot download from Sunsetter yet"
    )


def test_the_two_ways_of_learning_a_jar_was_refused_say_exactly_one_thing() -> None:
    """The tool saying so and a refusal arriving on a run that carried a jar are two provenances
    for one conclusion. Two lines in nearly the same words is how a table contradicts itself."""
    assert sentences.download_failed("cookies-refused", "Sunsetter") == sentences.download_failed(
        "login-failed", "Sunsetter"
    )


def test_a_wall_this_build_writes_is_a_wall_the_table_can_answer_for() -> None:
    """`wall-region` is in the table because a site declares one: a line keyed on a code nothing
    writes is a line nobody ever sees."""
    assert "region" in {wall.kind for record in SITES for wall in record.walls}


# --- Every failure names the site and the code, and what it means there ---------------------------
#
# Each case below is the raw output a tool gave for a real address, run without cookies (the
# address itself is replaced; the tool's words are not). One test per code per site the table
# covers, so a meaning that stops being read fails by name.

#: yt-dlp's whole answer for a video Pornhub has taken down, as a log keeps it, including
#: the traceback detailed logging adds after the ERROR line.
_PORNHUB_410 = (
    "ERROR: [PornHub] abc: Unable to download webpage: HTTP Error 410: Gone (caused by "
    "<HTTPError 410: Gone>)\n"
    '  File "C:\\Users\\[redacted]\\site-packages\\yt_dlp\\networking\\_requests.py", line 361\n'
    "    raise HTTPError(res, redirect_loop=max_redirects_exceeded)\n"
    "yt_dlp.networking.exceptions.HTTPError: HTTP Error 410: Gone"
)
_PORNHUB = "https://www.pornhub.com/view_video.php?viewkey=abc"
_X = "https://x.com/Sunsetter/status/1/video/1"


def test_pornhub_410_is_a_refusal_of_how_the_tool_connects_said_with_the_code() -> None:
    """A Pornhub address answered 410 Gone every time, and the row says what that means there.

    Not "the video has been removed" and not a region: the site answers 410 to every video address,
    invented ones included, from every region, a tunnel exiting where the site serves included,
    while the same request made with a browser's handshake gets the video. The meaning is how the
    tool connects, and a tunnel is not offered."""
    found = failures.classify(_PORNHUB, _PORNHUB_410)
    assert found is not None
    assert found.sentence == (
        "Pornhub answered 410 Gone: it refuses the downloader's own way of connecting, so Sift "
        "has to connect the way a browser does."
    )
    assert found.code == "http-410"
    assert found.tier == 3  # the site's own meaning, from its record
    assert found.final
    assert not found.a_tunnel_would_help
    assert not found.cookies_would_help


def test_a_sites_own_reading_of_a_code_decides_whether_a_tunnel_is_offered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The site's word wins over the code's in both directions. No real record exercises this
    override, so this plants a site reading that refuses a tunnel for a code that would otherwise
    offer one."""
    here = Meaning(
        code="http-403", meaning="it refuses this", seen="planted", a_tunnel_would_help=False
    )
    monkeypatch.setattr(failures, "_meaning_on", lambda _record, _code: here)
    assert failures.explain_status(_PORNHUB, 403).a_tunnel_would_help is False
    monkeypatch.undo()
    assert failures.explain_status(_UNKNOWN, 403).a_tunnel_would_help is True


def test_a_410_on_a_site_with_no_meaning_of_its_own_still_says_gone_permanently() -> None:
    found = failures.classify(_UNKNOWN, "ERROR: HTTP Error 410: Gone")
    assert found is not None
    assert found.tier == 2
    assert found.sentence.endswith("answered 410 Gone: the post has been removed permanently.")
    assert found.final


def test_x_unavailable_from_gallery_dl_is_private_or_deleted() -> None:
    found = failures.classify(_X, "[1/1] x\n[twitter][error] 'Unavailable'")
    assert found is not None
    assert found.sentence == "X: the post is private or deleted (gallery-dl: Unavailable)."
    assert (found.code, found.tier, found.final) == ("gone", 3, True)


def test_x_unavailable_from_yt_dlp_is_the_same_answer() -> None:
    """X's fallback tool says it its own way; the reading is the same code with the other quote."""
    found = failures.classify(_X, "ERROR: [twitter] 1: Video #1 is unavailable")
    assert found is not None
    assert found.sentence == "X: the post is private or deleted (yt-dlp: Video #1 is unavailable)."
    assert found.code == "gone"


def test_hqporner_404_says_the_code_and_what_it_means() -> None:
    found = failures.classify(
        "https://hqporner.com/hdporn/1-a.html",
        "ERROR: [generic] Unable to download webpage: HTTP Error 404: Not Found (caused by "
        "<HTTPError 404: Not Found>)",
    )
    assert found is not None
    assert found.sentence == (
        "HQporner answered 404 Not Found: there is nothing at that address; it was removed, or "
        "never existed."
    )
    assert found.final


def test_redtube_unreadable_page_quotes_the_tool_without_its_advice() -> None:
    found = failures.classify(
        "https://www.redtube.com/1",
        "ERROR: [RedTube] 1: Unable to extract video URL; please report this issue on  "
        "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. "
        "Confirm you are on the latest version using  yt-dlp -U",
    )
    assert found is not None
    assert found.code == "unreadable-page"
    assert found.sentence.endswith("(yt-dlp: Unable to extract video URL).")
    assert "github" not in found.sentence
    assert not found.final  # an updated tool reads a changed page


def test_youtube_403_is_the_code_and_the_cookies_door() -> None:
    """Tier 1: no meaning written for a 403, so the reader one layer up keeps its own words and
    appends this sentence: the cookies door stays exactly where it was."""
    found = failures.classify(
        _YOUTUBE, "ERROR: unable to download video data: HTTP Error 403: Forbidden"
    )
    assert found is not None
    assert (found.code, found.tier) == ("http-403", 1)
    assert found.sentence == "YouTube answered 403 Forbidden."
    assert found.cookies_would_help


def test_instagram_login_wall_keeps_its_wall_and_carries_the_tool_quote() -> None:
    found = failures.classify(
        "https://www.instagram.com/reels/abc/",
        "ERROR: [Instagram] abc: Requested content is not available, rate-limit reached or login "
        "required. Use --cookies-from-browser or --cookies for the authentication.",
    )
    assert found is not None
    assert found.code == "wall-login"
    assert found.said == (
        "yt-dlp: Requested content is not available, rate-limit reached or login required"
    )


def test_a_private_video_is_final_and_does_not_ask_for_a_login() -> None:
    found = failures.classify(_UNKNOWN, "ERROR: [youtube] abc: Private video. Sign in if granted")
    assert found is not None
    assert found.code == "private"
    assert found.final
    assert not found.cookies_would_help


def test_a_region_block_named_by_the_tool_is_the_region_code_and_says_tunnel() -> None:
    found = failures.classify(
        _UNKNOWN, "ERROR: [x] abc: This video is not available in your country"
    )
    assert found is not None
    assert found.code == "wall-region"
    assert found.tier == 2
    assert "tunnel" in found.sentence
    assert not found.a_tunnel_would_help  # the sentence says it; the flag would say it twice


def test_a_rate_limit_named_by_the_tool_says_try_again_and_is_not_final() -> None:
    found = failures.classify(_UNKNOWN, "ERROR: [x] abc: rate limit exceeded, slow down")
    assert found is not None
    assert found.code == "rate-limited"
    assert "try again" in found.sentence
    assert not found.final


def test_a_quote_never_carries_an_address_or_a_path() -> None:
    """A stored sentence is drawn on a screen and copied into bug reports. The address is on the
    row already and a path names the account it sits under."""
    said = failures.tool_words(
        "ERROR: https://cdn.example/a/b unable to open for writing: [Errno 2] No such file or "
        "directory: 'C:\\Users\\Sunsetter\\AppData\\x.mp4'"
    )
    assert said == "yt-dlp: unable to open for writing: [Errno 2] No such file or directory"
    assert failures.tool_words("ERROR: failed at /home/Sunsetter/clips/a.mp4") == (
        "yt-dlp: failed at"
    )


def test_a_long_quote_is_cut_at_a_word() -> None:
    said = failures.tool_words("ERROR: " + "word " * 40)
    assert said is not None
    assert said.endswith("...")
    assert len(said) < 110


def test_every_meaning_a_site_declares_is_a_code_the_reader_writes_and_was_seen() -> None:
    """A meaning keyed on a code nothing writes is a line nobody sees; one with no `seen` is a
    claim nobody made: the rule the walls are held to."""
    readable = {code for code, *_rest in failures._TOOL_REASONS} | {
        code for code, *_rest in failures._CONDITIONS
    }
    for record in SITES:
        for entry in record.failure_words:
            assert entry.seen.strip(), (record.key, entry.code)
            assert entry.meaning == entry.meaning.strip().rstrip("."), entry.meaning
            if entry.code.startswith("http-"):
                assert entry.code[len("http-") :].isdigit(), entry.code
            else:
                assert entry.code in readable, entry.code


def test_a_status_the_site_gave_a_meaning_stands_over_the_table_line() -> None:
    """The row's table line is written about the code on every site; a tier 3 reading is written
    about THIS site, and the table stands aside for it. Walls keep their line at any tier."""
    assert sentences.download_failed("http-410", "Sunsetter", 3) is None
    assert sentences.download_failed("http-410", "Sunsetter", 2) == (
        "Sunsetter answered 410 Gone: the post has been removed"
    )
    assert sentences.download_failed("wall-age", "Sunsetter", 3) is not None


def test_every_status_line_in_the_table_names_its_code() -> None:
    """A row answers "why", and a status line that hid the number would answer it less well than
    the stored sentence it replaces."""
    for code in sentences.DOWNLOAD_SAID:
        if code.startswith("http-"):
            said = sentences.download_failed(code, "Sunsetter")
            assert said is not None
            assert f"answered {code[len('http-') :]} " in said, said


def test_the_row_draws_the_sites_own_status_sentence_rather_than_the_table_line() -> None:
    """The site's own meaning for a code stands over the line written about the code everywhere.

    Read from the site's record when the row is shown (saved text is rendered when shown), rather
    than by keeping whatever sentence was stored at tier 3, so a row stored
    before the site's meaning was written, or with words since retired, says what is known now. An
    address the catalog does not know gets the table's line, whatever tier was stored.
    """
    from sift.slices.download.router import _item
    from sift.slices.download.service import DownloadView

    def row(tier: int, url: str | None) -> DownloadView:
        return DownloadView(
            id="d1",
            status="failed",
            dest_folder_id=None,
            site=None,
            username=None,
            asset_id=None,
            error="A sentence stored when it failed.",
            created_at=0,
            error_code="http-410",
            error_tier=tier,
            site_name="Pornhub",
            url=url,
        )

    address = "https://www.pornhub.com/view_video.php?viewkey=abc"
    reading = failures.reading_now(address, "http-410")
    assert reading is not None and reading.tier == 3
    assert _item(row(2, address)).sentence == reading.sentence
    assert _item(row(3, None)).sentence == "Pornhub answered 410 Gone: the post has been removed"


# --- the words a failed row reads, held to the screen's rules -------------------------------------


def test_a_refused_jar_names_the_site_and_the_screen_the_cookies_are_replaced_on() -> None:
    """The sentence sends somebody to where cookies are replaced, Settings > Sites and Tunnels >
    Cookies, and never to a screen that does not exist. The site is named because the row it lands
    on may be one of forty."""
    found = failures.classify(_A_FILE_HOST, "ERROR: login failed for this extractor")
    assert found is not None
    assert found.code == "login-failed"
    assert found.sentence == (
        "GoFile refused the saved cookies. Export them from your browser again and replace them in "
        "Settings > Sites and Tunnels > Cookies."
    )


def test_no_download_failure_sentence_puts_a_double_hyphen_on_screen() -> None:
    """No raw double-hyphen dash reaches a live row, including from sentences the display-dash
    ratchet cannot see: helpers that return a string under no copy-shaped name. Every one of them is read here, with the tables the ratchet does read, and
    each retry sentence says the same thing in the same words."""
    from sift.slices.download import jobs
    from sift.slices.download.sources import errors, fetcher
    from sift.slices.download.sources.downloader import Downloader

    tables = [
        *failures.NAMED_CODES.values(),
        *failures.STATUS_MEANS.values(),
        *(sentence for _code, _pattern, sentence, _tunnel in failures._CONDITIONS),
        *(meaning for _code, _pattern, meaning, _final, _tunnel in failures._TOOL_REASONS),
    ]
    helpers = [
        jobs._transient_message(),
        jobs._truncated_message(),
        jobs._paused_message(),
        jobs._disk_full_message(),
        jobs._animated_webp_message(),
        jobs._quarantined_message(),
        fetcher._FETCH_FAILED_MESSAGE,
        errors.busy_message("GoFile"),
        Downloader()._generic_message(_A_FILE_HOST),
        Downloader()._unsupported_message(_A_FILE_HOST),
    ]
    for said in [*tables, *helpers]:
        assert " -- " not in said, said
        assert "little while" not in said, said
    assert jobs._transient_message() == "The download did not finish. Try again in a few minutes."
    assert jobs._transient_message() == fetcher._FETCH_FAILED_MESSAGE


def test_a_code_recorded_without_its_output_is_read_from_the_code_and_the_site() -> None:
    """A wall is that site's wall of that kind; a condition and a named reason are their own
    sentences; anything the code alone cannot say is left to the stored sentence."""
    instagram = "https://www.instagram.com/p/abc/"
    wall = failures.reading_now(instagram, "wall-login")
    assert wall is not None
    assert (wall.tier, wall.sentence) == (
        3,
        "Cookies needed if download method is changed from Sift.",
    )
    assert failures.reading_now(instagram, "wall-region") is None
    assert failures.reading_now("https://example.com/a", "wall-login") is None
    full = failures.reading_now(instagram, "no-space")
    assert full is not None and full.tier == 2 and full.code == "no-space"
    gone = failures.reading_now("https://example.com/a", "gone")
    assert gone is not None and gone.tier == 2 and gone.sentence.endswith(".")
    assert failures.reading_now(instagram, None) is None
    assert failures.reading_now(instagram, "http-teapot") is None


def test_the_quote_is_the_last_failure_line_whichever_tool_wrote_it() -> None:
    output = "[twitter][error] 'Unavailable'\nERROR: [Example] abc123: Video is gone"
    assert failures.tool_words(output) == "yt-dlp: Video is gone"
    # Nothing left once the quotes are off is no quote at all.
    assert failures.tool_words("ERROR: ''") is None

"""
Tests for the collector's pure decision logic. The network and league halves
are exercised against the real ESPN API by dev/api_healthcheck.py; what lives
here is the logic a wrong answer would silently corrupt the database with.
"""
import gamedaybot.espn.collector as collector
import gamedaybot.web.theme as theme


def test_default_espn_logo_is_never_fetched():
    """ESPN's default_logos silhouette is 'no logo', not a logo."""
    url = "https://g.espncdn.com/lm-static/ffl/images/default_logos/19.svg"
    assert not collector.wants_logo_fetch(url, None)


def test_missing_url_is_never_fetched():
    assert not collector.wants_logo_fetch(None, None)
    assert not collector.wants_logo_fetch("", None)


def test_unchanged_url_is_not_refetched():
    url = "https://g.espncdn.com/lm-static/logo-packs/x.svg"
    assert not collector.wants_logo_fetch(url, url)


def test_new_or_changed_url_is_fetched():
    url = "https://mystique-api.fantasy.espn.com/apis/v1/domains/lm/images/abc"
    assert collector.wants_logo_fetch(url, None)
    assert collector.wants_logo_fetch(url, "https://x/old.png")


def test_monogram_initials_skip_emoji_decoration():
    assert theme.monogram_initials("💯 U MAD Bro? 💯") == "UM"
    assert theme.monogram_initials("First Down Syndrome") == "FD"
    assert theme.monogram_initials("Yikes(4)") == "Y"


def test_monogram_falls_back_to_the_first_character():
    assert theme.monogram_initials("💯💯") == "💯"


def test_monogram_data_uri_is_svg():
    uri = theme.monogram_data_uri("First Down Syndrome", "#6699DD")
    assert uri.startswith("data:image/svg+xml;base64,")

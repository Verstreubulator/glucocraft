# License: AGPL-3.0-only (LICENSE).
"""regional.py: first day of the week, decimal separator, A1C / GMI unit and PDF paper
size - "Automatic" from Home Assistant's country / language plus explicit choices; number formatting (halves away from
zero, never "-0.0"); A1C conversions against the IFCC-NGSP master equation; the configuration() contract keys."""

from __future__ import annotations

from pathlib import Path

import pytest
from glucocraft import regional as rg_mod
from glucocraft.regional import (
    Regional,
    a1c_to_mmol_mol,
    a1c_to_percent,
    regional_from_settings,
    resolve_a1c_unit,
    resolve_date,
    resolve_decimal_separator,
    resolve_first_weekday,
    resolve_paper_size,
    resolve_time,
)
from glucocraft.units import gmi_mmol_mol, round_half_up

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


# ------------------------------------------------------------------------------------------------ first day of the week
@pytest.mark.parametrize(
    "country,expected",
    [
        # Sunday (CLDR weekData)
        ("US", SUN),
        ("CA", SUN),
        ("MX", SUN),
        ("BR", SUN),
        ("JP", SUN),
        ("KR", SUN),
        ("TW", SUN),
        ("HK", SUN),
        ("IL", SUN),
        ("PH", SUN),
        ("TH", SUN),
        ("ZA", SUN),
        ("SA", SUN),
        ("PT", SUN),
        ("PR", SUN),
        ("CO", SUN),
        # half-hour and 45-minute time zones play no part: India (+05:30) and Nepal (+05:45) start on Sunday,
        # Sri Lanka (+05:30) and Australia (Adelaide +09:30) on Monday
        ("IN", SUN),
        ("NP", SUN),
        ("LK", MON),
        ("AU", MON),
        # Monday
        ("DE", MON),
        ("FR", MON),
        ("GB", MON),
        ("IE", MON),
        ("NZ", MON),
        ("CN", MON),
        ("RU", MON),
        ("SE", MON),
        ("ES", MON),
        ("IT", MON),
        ("NL", MON),
        ("PL", MON),
        ("AR", MON),
        ("CL", MON),
        ("TR", MON),
        ("VN", MON),
        # Saturday
        ("AE", SAT),
        ("EG", SAT),
        ("IR", SAT),
        ("IQ", SAT),
        ("KW", SAT),
        ("QA", SAT),
        ("DZ", SAT),
        ("AF", SAT),
        # Friday (Maldives; no explicit choice offers it)
        ("MV", FRI),
        # unknown country: the CLDR default (Monday)
        ("ZZ", MON),
    ],
)
def test_first_weekday_automatic_by_country(country, expected):
    assert resolve_first_weekday("auto", country) == expected
    assert resolve_first_weekday(None, country.lower()) == expected  # case and a missing setting
    assert resolve_first_weekday("", f" {country} ") == expected  # whitespace


def test_first_weekday_without_country_is_sunday_like_the_other_us_defaults():
    assert resolve_first_weekday("auto", None) == SUN
    assert resolve_first_weekday("auto", "") == SUN


@pytest.mark.parametrize("setting,expected", [("monday", MON), ("sunday", SUN), ("saturday", SAT)])
@pytest.mark.parametrize("country", ["US", "DE", "AE", "MV", None])
def test_first_weekday_explicit_choice_wins(setting, expected, country):
    assert resolve_first_weekday(setting, country) == expected


def test_first_weekday_unknown_setting_is_automatic():
    assert resolve_first_weekday("tuesday", "DE") == MON
    assert resolve_first_weekday("6", "DE") == MON


# ------------------------------------------------------------------------------------------------ decimal separator
@pytest.mark.parametrize(
    "country",
    [
        "DE",
        "AT",
        "FR",
        "ES",
        "IT",
        "NL",
        "BE",
        "PT",
        "PL",
        "CZ",
        "SK",
        "HU",
        "RO",
        "BG",
        "HR",
        "SI",
        "GR",
        "DK",
        "NO",
        "SE",
        "FI",
        "IS",
        "EE",
        "LV",
        "LT",
        "RU",
        "UA",
        "TR",
        "BR",
        "AR",
        "CL",
        "CO",
        "UY",
        "VE",
        "ID",
        "VN",
        "ZA",
        "LU",
    ],
)
def test_decimal_comma_countries(country):
    assert resolve_decimal_separator("auto", country) == ","
    assert resolve_decimal_separator("auto", country, "en") == ","  # the country decides there


@pytest.mark.parametrize(
    "country",
    [
        "US",
        "GB",
        "IE",
        "AU",
        "NZ",
        "MX",
        "JP",
        "CN",
        "KR",
        "IN",
        "IL",
        "SG",
        "PH",
        "TH",
        "MY",
        "PE",
        "GT",
        "PR",
        "MT",
        "LI",
    ],
)
def test_decimal_point_countries(country):
    assert resolve_decimal_separator("auto", country) == "."
    assert resolve_decimal_separator("auto", country, "de") == "."  # the country decides there


@pytest.mark.parametrize(
    "country,language,expected",
    [
        ("CH", "de", "."),
        ("CH", "it", "."),
        ("CH", "fr", ","),
        ("CH", "en", "."),
        ("CH", None, "."),
        ("CA", "en", "."),
        ("CA", "fr", ","),
        ("CA", "fr-CA", ","),
        ("CA", "fr_CA", ","),
        ("CA", None, "."),
    ],
)
def test_decimal_separator_by_language_in_multilingual_countries(country, language, expected):
    assert resolve_decimal_separator("auto", country, language) == expected


@pytest.mark.parametrize(
    "language,expected",
    [
        ("de", ","),
        ("fr", ","),
        ("pt-BR", ","),
        ("nb", ","),
        ("sv", ","),
        ("en", "."),
        ("en-GB", "."),
        ("ja", "."),
        ("zh-Hans", "."),
        (None, "."),
        ("", "."),
    ],
)
def test_decimal_separator_without_country_from_language(language, expected):
    assert resolve_decimal_separator("auto", None, language) == expected


@pytest.mark.parametrize("setting,expected", [("point", "."), ("comma", ",")])
@pytest.mark.parametrize("country", ["US", "DE", "CH", None])
def test_decimal_separator_explicit_choice_wins(setting, expected, country):
    assert resolve_decimal_separator(setting, country, "fr") == expected


# ------------------------------------------------------------------------------------------------ A1C / GMI unit
@pytest.mark.parametrize(
    "country,expected",
    [
        ("GB", "mmol_mol"),
        ("IE", "mmol_mol"),
        ("NZ", "mmol_mol"),
        ("SE", "mmol_mol"),
        ("DK", "mmol_mol"),
        ("NO", "mmol_mol"),
        ("FI", "mmol_mol"),
        ("NL", "mmol_mol"),
        ("DE", "both"),
        ("AT", "both"),
        ("CH", "both"),
        ("IT", "both"),
        ("FR", "both"),
        ("ES", "both"),
        ("AU", "both"),
        ("US", "percent"),
        ("CA", "percent"),
        ("MX", "percent"),
        ("BR", "percent"),
        ("JP", "percent"),
        ("IN", "percent"),
        ("CN", "percent"),
        ("ZZ", "percent"),
    ],
)
def test_a1c_unit_automatic_by_country(country, expected):
    assert resolve_a1c_unit("auto", country) == expected
    assert resolve_a1c_unit("auto", country, "mmol/L") == expected  # with a country the glucose unit is ignored


def test_a1c_unit_without_country_follows_the_glucose_unit():
    assert resolve_a1c_unit("auto", None, "mmol/L") == "both"
    assert resolve_a1c_unit("auto", None, "mg/dL") == "percent"
    assert resolve_a1c_unit("auto", None, None) == "percent"


@pytest.mark.parametrize("setting", ["percent", "mmol_mol", "both"])
def test_a1c_unit_explicit_choice_wins(setting):
    for country in ("US", "GB", "DE", None):
        assert resolve_a1c_unit(setting, country, "mmol/L") == setting


# ------------------------------------------------------------------------------------------------ paper size
@pytest.mark.parametrize(
    "country", ["US", "CA", "MX", "PH", "CL", "CO", "CR", "GT", "NI", "PA", "PR", "SV", "VE", "BZ", "GU", "VI", "AS"]
)
def test_letter_countries(country):
    assert resolve_paper_size("auto", country) == "letter"


@pytest.mark.parametrize(
    "country",
    ["BR", "AR", "PE", "UY", "EC", "BO", "PY", "DE", "GB", "FR", "JP", "CN", "IN", "AU", "NZ", "ZA", "RU", "ZZ"],
)
def test_a4_countries(country):
    assert resolve_paper_size("auto", country) == "a4"


def test_paper_size_without_country_and_explicit_choice():
    assert resolve_paper_size("auto", None) == "letter"
    assert resolve_paper_size("a4", "US") == "a4"
    assert resolve_paper_size("letter", "DE") == "letter"
    assert resolve_paper_size("legal", "DE") == "a4"  # unknown setting = automatic


# ------------------------------------------------------------------------------------------------ the contract
CONTRACT = {"time_format", "date_format", "first_weekday", "decimal_separator", "a1c_unit", "paper_size"}


def test_as_dict_is_exactly_the_contract():
    d = Regional().as_dict()
    assert set(d) == CONTRACT
    assert d == {
        "time_format": "12",
        "date_format": "MDY",
        "first_weekday": 6,
        "decimal_separator": ".",
        "a1c_unit": "percent",
        "paper_size": "letter",
    }


@pytest.mark.parametrize(
    "country,language,unit,expected",
    [
        (
            "US",
            "en",
            "mg/dL",
            {
                "time_format": "12",
                "date_format": "MDY",
                "first_weekday": 6,
                "decimal_separator": ".",
                "a1c_unit": "percent",
                "paper_size": "letter",
            },
        ),
        (
            "DE",
            "de",
            "mg/dL",
            {
                "time_format": "24",
                "date_format": "DMY",
                "first_weekday": 0,
                "decimal_separator": ",",
                "a1c_unit": "both",
                "paper_size": "a4",
            },
        ),
        (
            "GB",
            "en",
            "mmol/L",
            {
                "time_format": "24",
                "date_format": "DMY",
                "first_weekday": 0,
                "decimal_separator": ".",
                "a1c_unit": "mmol_mol",
                "paper_size": "a4",
            },
        ),
        (
            "CA",
            "fr",
            "mmol/L",
            {
                "time_format": "12",
                "date_format": "YMD",
                "first_weekday": 6,
                "decimal_separator": ",",
                "a1c_unit": "percent",
                "paper_size": "letter",
            },
        ),
        (
            "AE",
            "ar",
            "mg/dL",
            {
                "time_format": "24",
                "date_format": "DMY",
                "first_weekday": 5,
                "decimal_separator": ".",
                "a1c_unit": "percent",
                "paper_size": "a4",
            },
        ),
        (
            "IN",
            "en",
            "mg/dL",
            {
                "time_format": "12",
                "date_format": "DMY",
                "first_weekday": 6,
                "decimal_separator": ".",
                "a1c_unit": "percent",
                "paper_size": "a4",
            },
        ),
    ],
)
def test_regional_from_settings_all_automatic(country, language, unit, expected):
    settings = {k: "auto" for k in CONTRACT}
    assert regional_from_settings(settings, country, language, unit).as_dict() == expected
    assert regional_from_settings({}, country, language, unit).as_dict() == expected  # missing = automatic


def test_regional_from_settings_explicit():
    s = {
        "time_format": "24",
        "date_format": "YMD",
        "first_weekday": "saturday",
        "decimal_separator": "comma",
        "a1c_unit": "mmol_mol",
        "paper_size": "a4",
    }
    assert regional_from_settings(s, "US", "en").as_dict() == {
        "time_format": "24",
        "date_format": "YMD",
        "first_weekday": 5,
        "decimal_separator": ",",
        "a1c_unit": "mmol_mol",
        "paper_size": "a4",
    }


def test_from_dict_round_trip_and_defaults():
    r = Regional("24", "DMY", 0, ",", "both", "a4")
    assert Regional.from_dict(r.as_dict()).as_dict() == r.as_dict()
    assert Regional.from_dict(None).as_dict() == Regional().as_dict()
    assert Regional.from_dict({"time_format": "24"}).as_dict()["first_weekday"] == 6
    assert Regional.from_dict({"first_weekday": True}).first_weekday == 6  # a bool is not a weekday
    assert Regional(first_weekday=13).first_weekday == 6  # modulo 7
    assert Regional(decimal_separator=";").decimal == "."
    assert Regional(a1c_unit="x").a1c_unit == "percent" and Regional(paper_size="x").paper == "letter"


def test_time_and_date_resolution_unchanged():
    assert (
        resolve_time("auto", "US") == "12" and resolve_time("auto", "DE") == "24" and resolve_time("24", "US") == "24"
    )
    assert (
        resolve_date("auto", "US") == "MDY"
        and resolve_date("auto", "JP") == "YMD"
        and resolve_date("auto", "FR") == "DMY"
    )


# ------------------------------------------------------------------------------------------------ weeks
@pytest.mark.parametrize(
    "first,names",
    [
        (MON, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]),
        (SUN, ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]),
        (SAT, ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]),
        (FRI, ["Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu"]),
    ],
)
def test_week_order_and_lead_days(first, names):
    r = Regional(first_weekday=first)
    assert r.weekday_names() == names
    assert r.weekday_names(short=False)[0].startswith(names[0])
    assert r.week_days()[0] == first and sorted(r.week_days()) == list(range(7))
    for wd in range(7):
        lead = r.lead_days(wd)
        assert 0 <= lead < 7 and r.week_days()[lead] == wd  # the day lands in its own column


def test_lead_days_october_2026():
    # 1 October 2026 is a Thursday
    assert Regional(first_weekday=MON).lead_days(THU) == 3
    assert Regional(first_weekday=SUN).lead_days(THU) == 4
    assert Regional(first_weekday=SAT).lead_days(THU) == 5
    assert Regional(first_weekday=THU).lead_days(THU) == 0


# ------------------------------------------------------------------------------------------------ numbers
@pytest.mark.parametrize(
    "value,decimals,point,comma",
    [
        (5.55, 1, "5.6", "5,6"),
        (1.25, 1, "1.3", "1,3"),
        (-1.25, 1, "-1.3", "-1,3"),
        (2.5, 0, "3", "3"),
        (-2.5, 0, "-3", "-3"),
        (0.15, 1, "0.2", "0,2"),
        (-0.15, 1, "-0.2", "-0,2"),
        (0.05, 1, "0.1", "0,1"),
        (-0.04, 1, "0.0", "0,0"),
        (-0.0, 1, "0.0", "0,0"),
        (-0.4, 0, "0", "0"),
        (18.75, 1, "18.8", "18,8"),
        (100, 1, "100.0", "100,0"),
        (337.8 / 18.016, 1, "18.8", "18,8"),
        (7.049999, 1, "7.0", "7,0"),
        (12345.678, 2, "12345.68", "12345,68"),
    ],
)
def test_num_rounds_halves_away_from_zero(value, decimals, point, comma):
    assert Regional().num(value, decimals) == point
    assert Regional(decimal_separator=",").num(value, decimals) == comma


@pytest.mark.parametrize(
    "value,expected", [(1.0, "+1.0"), (-1.0, "-1.0"), (0.0, "+0.0"), (-0.04, "+0.0"), (-0.05, "-0.1"), (4.95, "+5.0")]
)
def test_num_with_sign(value, expected):
    assert Regional().num(value, 1, sign=True) == expected
    assert Regional(decimal_separator=",").num(value, 1, sign=True) == expected.replace(".", ",")


def test_num_matches_units_round_half_up_everywhere():
    r = Regional()
    for i in range(-3000, 3001):
        x = i / 200.0  # every half at one and two decimals
        for d in (0, 1, 2):
            want = round_half_up(x, d)
            assert float(r.num(x, d)) == (0.0 if want == 0 else want), (x, d)


def test_num_none_and_g_format():
    r, c = Regional(), Regional(decimal_separator=",")
    assert r.num(None) == "-" and r.num(None, none="---") == "---"
    assert r.g(65.0) == "65" and c.g(12.5) == "12,5" and c.g(0) == "0" and c.g(-0.0) == "0" and r.g(None) == "-"
    assert c.g(33.25) == "33,25" and r.g(1e-5) == "1e-05"


def test_dec_only_replaces_points():
    c = Regional(decimal_separator=",")
    assert c.dec("3.9-10.0") == "3,9-10,0" and c.dec("70-180") == "70-180"
    assert Regional().dec("3.9") == "3.9"


# ------------------------------------------------------------------------------------------------ A1C
def _master_ifcc(ngsp: float) -> float:
    """The IFCC-NGSP master equation solved for IFCC: NGSP = 0.09148 x IFCC + 2.152."""
    return (ngsp - 2.152) / 0.09148


def test_a1c_conversion_agrees_with_the_master_equation():
    for i in range(40, 141):  # 4.0 % ... 14.0 %
        pct = i / 10
        assert abs(a1c_to_mmol_mol(pct) - _master_ifcc(pct)) < 0.05, pct
        assert abs(a1c_to_percent(a1c_to_mmol_mol(pct)) - pct) < 1e-9
    for mm in range(20, 131):
        assert abs(a1c_to_mmol_mol(a1c_to_percent(mm)) - mm) < 1e-9


@pytest.mark.parametrize(
    "pct,mm",
    [(5.0, 31), (5.7, 39), (6.0, 42), (6.5, 48), (7.0, 53), (7.5, 58), (8.0, 64), (9.0, 75), (10.0, 86), (12.0, 108)],
)
def test_a1c_reference_values(pct, mm):
    """The published conversion table values (whole mmol/mol)."""
    assert round_half_up(a1c_to_mmol_mol(pct)) == mm
    assert round_half_up(_master_ifcc(pct)) == mm
    assert Regional(a1c_unit="mmol_mol").a1c(pct) == f"{mm} mmol/mol"


def test_a1c_none():
    assert a1c_to_mmol_mol(None) is None and a1c_to_percent(None) is None
    assert Regional().a1c(None) == "-" and Regional().a1c_parts(None) == ("-", None)


def test_a1c_text_per_unit_and_separator():
    assert Regional(a1c_unit="percent").a1c(7.0) == "7.0%"
    assert Regional(a1c_unit="mmol_mol").a1c(7.0) == "53 mmol/mol"
    assert Regional(a1c_unit="both").a1c(7.0) == "7.0% (53 mmol/mol)"
    assert Regional(a1c_unit="both", decimal_separator=",").a1c(6.85) == "6,9% (51 mmol/mol)"
    assert Regional(a1c_unit="both").a1c_parts(7.0) == ("7.0%", "53 mmol/mol")
    # a value in mmol/mol known more precisely (GMI from the mean) is used as it is
    assert Regional(a1c_unit="mmol_mol").a1c(7.0, 54.0) == "54 mmol/mol"


def test_gmi_mmol_mol_from_the_mean_agrees_with_the_converted_percent():
    """Bergenstal et al. (2018) give both GMI formulas; they agree within half a mmol/mol on unrounded values."""
    for mean in range(70, 351, 5):
        pct = 3.31 + 0.02392 * mean
        assert abs(gmi_mmol_mol(mean) - a1c_to_mmol_mol(pct)) <= 0.6 + 0.5, mean  # 0.5: gmi_mmol_mol is whole


def test_no_glucocraft_imports_in_regional():
    """regional.py stays free of other GlucoCraft modules (the PDF, the report and the cards share it), except the
    language catalogues (i18n.py) and the one rounding rule (units.py), neither of which imports a GlucoCraft module."""
    src = Path(rg_mod.__file__).read_text(encoding="utf-8")
    assert src.count("from .") == 2 and "from .i18n import t" in src and "import glucocraft" not in src
    assert "from .units import round_half_up" in src and "def _half_up" not in src  # no second copy of the rule

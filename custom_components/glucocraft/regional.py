# License: AGPL-3.0-only (LICENSE).
"""Regional settings: time and date formats, first day of the week, decimal separator, A1C / GMI unit, paper size.

Each setting is Automatic (from Home Assistant's country) or an explicit choice, so that users in every country see
the formats they are used to.

Home Assistant keeps the time and date format per user profile, in the browser; the panels, the PDF and the
notifications cannot read them. GlucoCraft therefore has its own settings, used everywhere (dashboard, display card,
wall panels, report page and PDF, notifications), each "auto" by default: from Home Assistant's country (and, for the
decimal separator in countries with more than one convention, its language).

Country defaults follow the Unicode CLDR (common/supplemental/supplementalData.xml: timeData, weekData firstDay,
measurementData paperSize; common/main/*.xml: the short date patterns and the decimal symbol): 12-hour clock in the
countries below, 24-hour elsewhere; month-day-year in the US and its territories and the Philippines, year-month-day in
East Asia, Hungary, Lithuania, Sweden, Canada and South Africa, day-month-year elsewhere. Weekday and month names are
English (no translations yet). The A1C / GMI unit by country is a GlucoCraft rule (the laboratories' reporting units
after the IFCC standardization; both units where laboratories report both), see A1C_* below.

Formats (one set, also in the cards and the panels):
  clock          12: "7:46 AM"       24: "07:46"
  hour label     12: "6am"           24: "06:00"
  date (named)   MDY/YMD: "Oct 22"   DMY: "22 Oct"           (long: "October 22" / "22 October")
  date (numeric) MDY: "10/22"        DMY: "22/10"            YMD: "10-22"
  with the year  MDY: "10/22/2026"   DMY: "22/10/2026"       YMD: "2026-10-22"
  decimals       point: "5.6"        comma: "5,6"            (displayed text only; stored data, attributes and the API
                                                              always use the point)
  A1C / GMI      percent: "7.0%"     mmol_mol: "53 mmol/mol" both: "7.0% (53 mmol/mol)"
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .i18n import t
from .units import round_half_up

# The explicit choices of each setting (every setting also accepts "auto"); the resolved values next to them
TIME_12, TIME_24 = "12", "24"
DATE_MDY, DATE_DMY, DATE_YMD = "MDY", "DMY", "YMD"
WEEK_MONDAY, WEEK_SUNDAY, WEEK_SATURDAY = "monday", "sunday", "saturday"
WEEK_CHOICES = {WEEK_MONDAY: 0, WEEK_SUNDAY: 6, WEEK_SATURDAY: 5}  # resolved: 0 = Monday ... 6 = Sunday
DEC_POINT, DEC_COMMA = "point", "comma"
DEC_CHOICES = {DEC_POINT: ".", DEC_COMMA: ","}  # resolved: "." or ","
A1C_PERCENT, A1C_MMOL_MOL, A1C_BOTH = "percent", "mmol_mol", "both"
PAPER_LETTER, PAPER_A4 = "letter", "a4"

# CLDR timeData: regions whose preferred hour cycle is 12-hour ("h")
COUNTRIES_12H = frozenset({"US", "CA", "AU", "NZ", "IN", "PH", "PK", "BD", "EG", "SA"})
# short date order (CLDR short date patterns)
COUNTRIES_MDY = frozenset({"US", "PH", "PR", "GU", "VI", "AS", "MP", "UM", "FM", "MH", "PW"})
COUNTRIES_YMD = frozenset({"CN", "JP", "KR", "KP", "TW", "MN", "HU", "LT", "SE", "CA", "ZA", "IR", "BT"})
# CLDR weekData firstDay (Monday = "001", the default): the Sunday, Saturday and Friday (Maldives) regions
COUNTRIES_SUNDAY = frozenset(
    {
        "AG",
        "AS",
        "BD",
        "BR",
        "BS",
        "BT",
        "BW",
        "BZ",
        "CA",
        "CO",
        "DM",
        "DO",
        "ET",
        "GT",
        "GU",
        "HK",
        "HN",
        "ID",
        "IL",
        "IN",
        "JM",
        "JP",
        "KE",
        "KH",
        "KR",
        "LA",
        "MH",
        "MM",
        "MO",
        "MT",
        "MX",
        "MZ",
        "NI",
        "NP",
        "PA",
        "PE",
        "PH",
        "PK",
        "PR",
        "PT",
        "PY",
        "SA",
        "SG",
        "SV",
        "TH",
        "TT",
        "TW",
        "UM",
        "US",
        "VE",
        "VI",
        "WS",
        "YE",
        "ZA",
        "ZW",
    }
)
COUNTRIES_SATURDAY = frozenset(
    {"AE", "AF", "BH", "DJ", "DZ", "EG", "IQ", "IR", "JO", "KW", "LY", "OM", "QA", "SD", "SY"}
)
COUNTRIES_FRIDAY = frozenset({"MV"})
# CLDR decimal symbol of each country's main locale: a comma in these countries, a point elsewhere
COUNTRIES_DECIMAL_COMMA = frozenset(
    {
        # Europe (not CH / LI: de_CH and de_LI use the point; not GB, IE, MT)
        "AD",
        "AL",
        "AM",
        "AT",
        "AX",
        "AZ",
        "BA",
        "BE",
        "BG",
        "BY",
        "CY",
        "CZ",
        "DE",
        "DK",
        "EE",
        "ES",
        "FI",
        "FO",
        "FR",
        "GE",
        "GL",
        "GR",
        "HR",
        "HU",
        "IS",
        "IT",
        "KZ",
        "LT",
        "LU",
        "LV",
        "MC",
        "MD",
        "ME",
        "MK",
        "NL",
        "NO",
        "PL",
        "PT",
        "RO",
        "RS",
        "RU",
        "SE",
        "SI",
        "SJ",
        "SK",
        "SM",
        "TR",
        "UA",
        "UZ",
        "VA",
        "XK",
        # the Americas
        "AR",
        "BO",
        "BR",
        "CL",
        "CO",
        "CR",
        "EC",
        "PY",
        "UY",
        "VE",
        # Africa and Asia
        "AO",
        "CM",
        "CV",
        "DZ",
        "ID",
        "MA",
        "MZ",
        "TN",
        "VN",
        "ZA",
    }
)
# languages whose decimal symbol is a comma (CLDR): for countries with more than one convention, and without a country
LANGUAGES_DECIMAL_COMMA = frozenset(
    {
        "az",
        "be",
        "bg",
        "bs",
        "ca",
        "cs",
        "da",
        "de",
        "el",
        "es",
        "et",
        "eu",
        "fi",
        "fr",
        "gl",
        "hr",
        "hu",
        "hy",
        "id",
        "is",
        "it",
        "ka",
        "kk",
        "lt",
        "lv",
        "mk",
        "nb",
        "nl",
        "nn",
        "no",
        "pl",
        "pt",
        "ro",
        "ru",
        "sk",
        "sl",
        "sq",
        "sr",
        "sv",
        "tr",
        "uk",
        "uz",
        "vi",
    }
)
# countries whose decimal symbol depends on the language (CLDR en_CA / fr_CA, de_CH / fr_CH ...): the language decides
COUNTRIES_DECIMAL_BY_LANGUAGE = frozenset({"CA", "CH"})
# CLDR measurementData paperSize "US-Letter" (A4 elsewhere), plus the US territories
COUNTRIES_LETTER = frozenset(
    {"US", "CA", "MX", "PH", "BZ", "CL", "CO", "CR", "GT", "NI", "PA", "PR", "SV", "VE", "GU", "VI", "AS", "MP", "UM"}
)
# A1C / GMI unit (a GlucoCraft rule): mmol/mol where laboratories report IFCC units only, both
# where they report both units, percent elsewhere; without a country, from the glucose unit (mmol/L -> both)
A1C_COUNTRIES_MMOL_MOL = frozenset({"GB", "IE", "NZ", "SE", "DK", "NO", "FI", "IS", "NL"})
A1C_COUNTRIES_BOTH = frozenset(
    {
        "AT",
        "AU",
        "BE",
        "BG",
        "CH",
        "CY",
        "CZ",
        "DE",
        "EE",
        "ES",
        "FR",
        "GR",
        "HR",
        "HU",
        "IT",
        "LI",
        "LT",
        "LU",
        "LV",
        "MT",
        "PL",
        "PT",
        "RO",
        "SI",
        "SK",
    }
)

# A1C conversion: the IFCC-NGSP master equation, NGSP (%) = 0.09148 x IFCC (mmol/mol) + 2.152, in its usual form
# IFCC (mmol/mol) = 10.929 x (NGSP (%) - 2.15)
A1C_MMOL_PER_PCT = 10.929
A1C_PCT_OFFSET = 2.15

# The names of the months and weekdays, and the 12-hour clock's AM / PM, are words of the language catalogue
# (date.month.<1-12>, date.month_short.<1-12>, date.weekday.<0-6>, date.weekday_short.<0-6> with 0 = Monday,
# time.clock_12.am / .pm, time.hour_12.am / .pm); the order of the parts is the regional format below.


def month_name(month: int, short: bool = False) -> str:
    """Return the name of a month (1-12): "October", short "Oct"."""
    return t(f"date.month_short.{month}" if short else f"date.month.{month}")


def weekday_name(weekday: int, short: bool = False) -> str:
    """Return the name of a weekday (0 = Monday): "Monday", short "Mon"."""
    return t(f"date.weekday_short.{weekday}" if short else f"date.weekday.{weekday}")


def month_year(d: datetime) -> str:
    """Return a month with its year: "October 2026"."""
    return f"{month_name(d.month)} {d.year}"


def _cc(country: str | None) -> str:
    """Return a country code in upper case ("" for none)."""
    return (country or "").strip().upper()


def _lang(language: str | None) -> str:
    """Return the language part of a language tag in lower case ("fr-CA" -> "fr")."""
    return (language or "").strip().lower().replace("_", "-").split("-")[0]


def resolve_time(setting: str | None, country: str | None) -> str:
    """Return "12" or "24": the choice, or Automatic from the country (CLDR timeData; without a country: 12)."""
    if setting in (TIME_12, TIME_24):
        return setting
    return TIME_12 if (_cc(country) or "US") in COUNTRIES_12H else TIME_24


def resolve_date(setting: str | None, country: str | None) -> str:
    """Return "MDY", "DMY" or "YMD", the resolved order the cards, panels and the report read.

    The stored setting may be written in lower case ("mdy", the form Home Assistant's translation keys require) or in
    upper case; both mean the same choice. Anything else is Automatic, from the country.
    """
    choice = str(setting or "").upper()
    if choice in (DATE_MDY, DATE_DMY, DATE_YMD):
        return choice
    c = _cc(country) or "US"
    return DATE_MDY if c in COUNTRIES_MDY else DATE_YMD if c in COUNTRIES_YMD else DATE_DMY


def resolve_first_weekday(setting: str | None, country: str | None) -> int:
    """Return the first day of the week, 0 = Monday ... 6 = Sunday (Python's weekday()).

    Automatic: CLDR weekData (Friday for the Maldives, which no explicit choice offers). Without a country: Sunday, as
    the other formats default to the US.
    """
    if setting in WEEK_CHOICES:
        return WEEK_CHOICES[setting]
    c = _cc(country) or "US"
    return 6 if c in COUNTRIES_SUNDAY else 5 if c in COUNTRIES_SATURDAY else 4 if c in COUNTRIES_FRIDAY else 0


def resolve_decimal_separator(setting: str | None, country: str | None, language: str | None = None) -> str:
    """Return "." or ",".

    Automatic: the country's convention; in Canada and Switzerland, and without a country, Home Assistant's language
    decides (fr_CA / fr_CH: comma; en_CA, de_CH, it_CH: point).
    """
    if setting in DEC_CHOICES:
        return DEC_CHOICES[setting]
    c, lang = _cc(country), _lang(language)
    if c and c not in COUNTRIES_DECIMAL_BY_LANGUAGE:
        return "," if c in COUNTRIES_DECIMAL_COMMA else "."
    if c:  # Canada or Switzerland (COUNTRIES_DECIMAL_BY_LANGUAGE): French writes the comma
        return "," if lang == "fr" else "."
    return "," if lang in LANGUAGES_DECIMAL_COMMA else "."


def resolve_a1c_unit(setting: str | None, country: str | None, glucose_unit: str | None = None) -> str:
    """Return "percent", "mmol_mol" or "both" (a GlucoCraft rule for Automatic, see A1C_COUNTRIES_*)."""
    if setting in (A1C_PERCENT, A1C_MMOL_MOL, A1C_BOTH):
        return setting
    c = _cc(country)
    if c:
        return A1C_MMOL_MOL if c in A1C_COUNTRIES_MMOL_MOL else A1C_BOTH if c in A1C_COUNTRIES_BOTH else A1C_PERCENT
    return A1C_BOTH if glucose_unit == "mmol/L" else A1C_PERCENT


def resolve_paper_size(setting: str | None, country: str | None) -> str:
    """Return "letter" or "a4".

    Automatic: Letter in the CLDR US-Letter countries (and the US territories), A4 elsewhere; without a country:
    Letter (the other formats default to the US).
    """
    if setting in (PAPER_LETTER, PAPER_A4):
        return setting
    c = _cc(country) or "US"
    return PAPER_LETTER if c in COUNTRIES_LETTER else PAPER_A4


def a1c_to_mmol_mol(pct: float | None) -> float | None:
    """Convert A1C or GMI in percent (NGSP) to mmol/mol (IFCC), unrounded: 10.929 x (% - 2.15)."""
    return None if pct is None else A1C_MMOL_PER_PCT * (float(pct) - A1C_PCT_OFFSET)


def a1c_to_percent(mmol_mol: float | None) -> float | None:
    """Convert A1C in mmol/mol (IFCC) to percent (NGSP), unrounded: mmol/mol / 10.929 + 2.15."""
    return None if mmol_mol is None else float(mmol_mol) / A1C_MMOL_PER_PCT + A1C_PCT_OFFSET


# A number text that is a negative zero ("-0", "-0.0", "-0.00"): shown as zero (Regional.dec)
_NEGATIVE_ZERO = re.compile(r"^-(0(?:\.0+)?)$")


class Regional:
    """Formatting with the resolved settings: time format ("12"/"24"), date order ("MDY"/"DMY"/"YMD"), first day of
    the week (0-6), decimal separator ("."/","), A1C / GMI unit ("percent"/"mmol_mol"/"both") and paper size."""

    def __init__(
        self,
        time_format: str = TIME_12,
        date_format: str = DATE_MDY,
        first_weekday: int = 6,
        decimal_separator: str = ".",
        a1c_unit: str = A1C_PERCENT,
        paper_size: str = PAPER_LETTER,
    ) -> None:
        """Keep the resolved settings; an unknown value falls back to the default."""
        self.time = time_format if time_format in (TIME_12, TIME_24) else TIME_12
        order = str(date_format or "").upper()  # "dmy" and "DMY" are the same order
        self.date = order if order in (DATE_MDY, DATE_DMY, DATE_YMD) else DATE_MDY
        self.first_weekday = int(first_weekday) % 7
        self.decimal = "," if decimal_separator == "," else "."
        self.a1c_unit = a1c_unit if a1c_unit in (A1C_PERCENT, A1C_MMOL_MOL, A1C_BOTH) else A1C_PERCENT
        self.paper = paper_size if paper_size in (PAPER_LETTER, PAPER_A4) else PAPER_LETTER

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Regional:
        """Return a Regional from as_dict() (the report data, the configuration attributes); missing keys = defaults."""
        d = d or {}
        fw = d.get("first_weekday", 6)
        return cls(
            d.get("time_format", TIME_12),
            d.get("date_format", DATE_MDY),
            fw if isinstance(fw, int) and not isinstance(fw, bool) else 6,
            d.get("decimal_separator", "."),
            d.get("a1c_unit", A1C_PERCENT),
            d.get("paper_size", PAPER_LETTER),
        )

    # ---- time
    def clock(self, d: datetime) -> str:
        """Return a time of day: "7:46 AM" / "07:46"."""
        if self.time == TIME_24:
            return f"{d.hour:02d}:{d.minute:02d}"
        half = "am" if d.hour < 12 else "pm"
        return t(f"time.clock_12.{half}", time=f"{d.hour % 12 or 12}:{d.minute:02d}")

    def hour(self, h: int) -> str:
        """Return an hour label on a time axis: "6am" / "06:00"."""
        if self.time == TIME_24:
            return f"{h:02d}:00"
        return t(f"time.hour_12.{'am' if h < 12 else 'pm'}", hour=h % 12 or 12)

    # ---- dates
    def day_month(self, d: datetime, long: bool = False, year: bool = False) -> str:
        """Return a named date: "Oct 22" / "22 Oct" (long: "October 22" / "22 October"); with the year
        "October 22, 2025" / "22 October 2025"."""
        m = month_name(d.month, short=not long)
        if self.date == DATE_DMY:
            return f"{d.day} {m} {d.year}" if year else f"{d.day} {m}"
        return f"{m} {d.day}, {d.year}" if year else f"{m} {d.day}"

    def numeric(self, d: datetime, year: bool = False) -> str:
        """Return a numeric date: "10/22" / "22/10" / "10-22" (with the year: "10/22/2026" ...)."""
        if self.date == DATE_DMY:
            return f"{d.day:02d}/{d.month:02d}/{d.year}" if year else f"{d.day}/{d.month}"
        if self.date == DATE_YMD:
            return f"{d.year}-{d.month:02d}-{d.day:02d}" if year else f"{d.month:02d}-{d.day:02d}"
        return f"{d.month:02d}/{d.day:02d}/{d.year}" if year else f"{d.month}/{d.day}"

    def weekday_numeric(self, d: datetime, year: bool = False) -> str:
        """Return a date with its weekday: "Wed 10/22" / "Wed 22/10" / "Wed 10-22"."""
        return f"{weekday_name(d.weekday(), short=True)} {self.numeric(d, year)}"

    def stamp(self, d: datetime) -> str:
        """Return a date and time: "Wed 10/22 7:46 AM" / "Wed 22/10 07:46"."""
        return f"{self.weekday_numeric(d)} {self.clock(d)}"

    def stamp_year(self, d: datetime) -> str:
        """Return a date with the year and time: "Wed 10/22/2026 7:46 AM" / "Wed 22/10/2026 07:46"."""
        return f"{self.weekday_numeric(d, True)} {self.clock(d)}"

    # ---- weeks: wherever calendar weeks are shown or counted (rolling 7-day windows stay rolling)
    def week_days(self) -> list[int]:
        """Return the weekdays (0 = Monday) in display order, from the first day of the week."""
        return [(self.first_weekday + k) % 7 for k in range(7)]

    def weekday_names(self, short: bool = True) -> list[str]:
        """Return "Mon" ... "Sun" (or the full names) in display order."""
        return [weekday_name(k, short) for k in self.week_days()]

    def lead_days(self, weekday: int) -> int:
        """Return the empty cells before a day of this weekday (0 = Monday) in a row that starts on the first day."""
        return (int(weekday) - self.first_weekday) % 7

    # ---- numbers (displayed text only)
    def dec(self, text: str) -> str:
        """Return a number already formatted with a point, with the decimal separator in use; a negative zero ("-0",
        "-0.0") as zero.

        Only for number text: sentences ("et al.") must never pass through here.
        """
        text = _NEGATIVE_ZERO.sub(r"\1", text)
        return text.replace(".", ",") if self.decimal == "," else text

    def num(self, value: float | None, decimals: int = 1, sign: bool = False, none: str = "-") -> str:
        """Return a number with exactly `decimals` decimals, halves away from zero, the decimal separator in use.

        sign=True adds "+" to positive values (and zero). Never "-0.0" (round_half_up gives no negative zero).
        """
        if value is None:
            return none
        r = round_half_up(float(value), decimals)
        return self.dec(f"{r:+.{decimals}f}" if sign else f"{r:.{decimals}f}")

    def g(self, value: float | None, none: str = "-") -> str:
        """Return a number as Python's :g (no trailing zeros: 65.0 -> "65") with the decimal separator in use."""
        if value is None:
            return none
        v = float(value)
        return self.dec(f"{0.0 if v == 0 else v:g}")

    # ---- A1C and GMI: GMI and laboratory A1C everywhere they are shown
    def a1c_parts(self, pct: float | None, mmol_mol: float | None = None) -> tuple[str, str | None]:
        """Return (main text, second text or None) of an A1C / GMI value given in percent.

        mmol_mol: the value in mmol/mol when it is known more precisely than the conversion of the rounded percent
        (GMI: Bergenstal et al., 2018, from the mean glucose); otherwise converted (IFCC-NGSP master equation).
        """
        if pct is None:
            return "-", None
        mm = a1c_to_mmol_mol(pct) if mmol_mol is None else float(mmol_mol)
        p_txt = f"{self.num(pct, 1)}%"
        m_txt = f"{self.num(mm, 0)} mmol/mol"
        if self.a1c_unit == A1C_MMOL_MOL:
            return m_txt, None
        if self.a1c_unit == A1C_BOTH:
            return p_txt, m_txt
        return p_txt, None

    def a1c(self, pct: float | None, mmol_mol: float | None = None, none: str = "-") -> str:
        """Return an A1C / GMI value as text: "7.0%" / "53 mmol/mol" / "7.0% (53 mmol/mol)"."""
        if pct is None:
            return none
        main, second = self.a1c_parts(pct, mmol_mol)
        return f"{main} ({second})" if second else main

    def as_dict(self) -> dict[str, Any]:
        """Return the resolved values as the configuration attributes publish them (the dashboard, cards, panels)."""
        return {
            "time_format": self.time,
            "date_format": self.date,
            "first_weekday": self.first_weekday,
            "decimal_separator": self.decimal,
            "a1c_unit": self.a1c_unit,
            "paper_size": self.paper,
        }


def regional_from_settings(
    settings: dict[str, Any] | None, country: str | None, language: str | None = None, glucose_unit: str | None = None
) -> Regional:
    """Return the Regional of a configuration (the six settings, each "auto" or a choice).

    Automatic settings are resolved with Home Assistant's country and language (and the glucose unit, for the A1C
    unit without a country).
    """
    s = settings or {}
    return Regional(
        resolve_time(s.get("time_format"), country),
        resolve_date(s.get("date_format"), country),
        resolve_first_weekday(s.get("first_weekday"), country),
        resolve_decimal_separator(s.get("decimal_separator"), country, language),
        resolve_a1c_unit(s.get("a1c_unit"), country, glucose_unit),
        resolve_paper_size(s.get("paper_size"), country),
    )

"""Scheduled FOMC policy-decision dates, 2009-2027.

Source: federalreserve.gov -- fomchistorical{2009..2020}.htm and
fomccalendars.htm (2021-2027), fetched 2026-09-13. Each entry is the LAST day
of a scheduled meeting, which is when the statement is released.

Excluded on purpose, because they are exactly what `/market_trend` exists to
detect: conference calls, notation votes, and meetings the Fed marks
"(unscheduled)". The March 17-18, 2020 meeting is excluded as "(cancelled)" --
keeping it would put a scheduled date two days after the 2020-03-16 emergency
cut and hide it.

Starts in 2009 because FRED's DFEDTARU (the target-range upper bound) starts on
2008-12-16. Extend this list when the Fed publishes a new year: once the last
date here has passed, `classify_rate_cuts` reports `unknown` rather than
guessing.
"""
from datetime import date

SCHEDULED_DECISIONS: tuple[date, ...] = (
    # 2009
    date(2009, 1, 28), date(2009, 3, 18), date(2009, 4, 29), date(2009, 6, 24), date(2009, 8, 12), date(2009, 9, 23), date(2009, 11, 4), date(2009, 12, 16),
    # 2010
    date(2010, 1, 27), date(2010, 3, 16), date(2010, 4, 28), date(2010, 6, 23), date(2010, 8, 10), date(2010, 9, 21), date(2010, 11, 3), date(2010, 12, 14),
    # 2011
    date(2011, 1, 26), date(2011, 3, 15), date(2011, 4, 27), date(2011, 6, 22), date(2011, 8, 9), date(2011, 9, 21), date(2011, 11, 2), date(2011, 12, 13),
    # 2012
    date(2012, 1, 25), date(2012, 3, 13), date(2012, 4, 25), date(2012, 6, 20), date(2012, 8, 1), date(2012, 9, 13), date(2012, 10, 24), date(2012, 12, 12),
    # 2013
    date(2013, 1, 30), date(2013, 3, 20), date(2013, 5, 1), date(2013, 6, 19), date(2013, 7, 31), date(2013, 9, 18), date(2013, 10, 30), date(2013, 12, 18),
    # 2014
    date(2014, 1, 29), date(2014, 3, 19), date(2014, 4, 30), date(2014, 6, 18), date(2014, 7, 30), date(2014, 9, 17), date(2014, 10, 29), date(2014, 12, 17),
    # 2015
    date(2015, 1, 28), date(2015, 3, 18), date(2015, 4, 29), date(2015, 6, 17), date(2015, 7, 29), date(2015, 9, 17), date(2015, 10, 28), date(2015, 12, 16),
    # 2016
    date(2016, 1, 27), date(2016, 3, 16), date(2016, 4, 27), date(2016, 6, 15), date(2016, 7, 27), date(2016, 9, 21), date(2016, 11, 2), date(2016, 12, 14),
    # 2017
    date(2017, 2, 1), date(2017, 3, 15), date(2017, 5, 3), date(2017, 6, 14), date(2017, 7, 26), date(2017, 9, 20), date(2017, 11, 1), date(2017, 12, 13),
    # 2018
    date(2018, 1, 31), date(2018, 3, 21), date(2018, 5, 2), date(2018, 6, 13), date(2018, 8, 1), date(2018, 9, 26), date(2018, 11, 8), date(2018, 12, 19),
    # 2019
    date(2019, 1, 30), date(2019, 3, 20), date(2019, 5, 1), date(2019, 6, 19), date(2019, 7, 31), date(2019, 9, 18), date(2019, 10, 30), date(2019, 12, 11),
    # 2020 -- seven, not eight: March 17-18 was cancelled (see module docstring)
    date(2020, 1, 29), date(2020, 4, 29), date(2020, 6, 10), date(2020, 7, 29), date(2020, 9, 16), date(2020, 11, 5), date(2020, 12, 16),
    # 2021
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28), date(2021, 6, 16), date(2021, 7, 28), date(2021, 9, 22), date(2021, 11, 3), date(2021, 12, 15),
    # 2022
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4), date(2022, 6, 15), date(2022, 7, 27), date(2022, 9, 21), date(2022, 11, 2), date(2022, 12, 14),
    # 2023
    date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3), date(2023, 6, 14), date(2023, 7, 26), date(2023, 9, 20), date(2023, 11, 1), date(2023, 12, 13),
    # 2024
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1), date(2024, 6, 12), date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    # 2025
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18), date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    # 2026
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    # 2027
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9), date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 12, 8),
)

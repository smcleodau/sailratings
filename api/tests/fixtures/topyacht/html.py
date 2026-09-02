"""Raw TopYacht HTML fixture bodies (DP-06-02).

These preserve representative source variants and breakage mutations as
raw HTML strings.  They are served by the in-process
:class:`~irc_data.sources.fake_adapter.FakeHttpServer` so tests make no
network calls.

The structure mirrors the real TopYacht static-HTML tree:

    /results/{year}/{division}/index.htm
    /results/{year}/{division}/{series}/series.htm
    /results/{year}/{division}/{series}/{nn}RGrp{g}.htm
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Index page — lists series with links to series.htm
# ---------------------------------------------------------------------------

INDEX_HTM = """<!DOCTYPE html>
<html>
<head><title>Hamilton Island Race Week 2024</title></head>
<body>
<p class="heading1">Hamilton Island Race Week 2024 — Results Index</p>
<table class="centre_index_table">
  <tr><td>Series</td><td>Type</td></tr>
  <tr><td><a href="rategold/series.htm">Rating Gold</a></td><td>IRC</td></tr>
  <tr><td><a href="pass/series.htm">Passage</a></td><td>PHS</td></tr>
</table>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Series page — table with an IRC column linking to race result pages
# ---------------------------------------------------------------------------

SERIES_HTM = """<!DOCTYPE html>
<html>
<head><title>Rating Gold</title></head>
<body>
<p class="heading1">Rating Gold Series</p>
<table class="centre_index_table">
  <tr><td>Race</td><td>PHS</td><td>IRC</td><td>ORC</td><td>Entrants</td></tr>
  <tr>
    <td>Race 1</td>
    <td><a href="01RGrp1.htm">PHS</a></td>
    <td><a href="01RGrp2.htm">IRC</a></td>
    <td><a href="01RGrp3.htm">ORC</a></td>
    <td>12</td>
  </tr>
  <tr>
    <td>Race 2</td>
    <td><a href="02RGrp1.htm">PHS</a></td>
    <td><a href="02RGrp2.htm">IRC</a></td>
    <td><a href="02RGrp3.htm">ORC</a></td>
    <td>12</td>
  </tr>
</table>
</body>
</html>
"""

# A second series for incremental / multi-page discovery tests.
SERIES_HTM_B = """<!DOCTYPE html>
<html>
<head><title>Passage Series</title></head>
<body>
<p class="heading1">Passage Series</p>
<table class="centre_index_table">
  <tr><td>Race</td><td>PHS</td><td>IRC</td><td>Entrants</td></tr>
  <tr>
    <td>Race 1</td>
    <td><a href="01RGrp1.htm">PHS</a></td>
    <td><a href="01RGrp2.htm">IRC</a></td>
    <td>8</td>
  </tr>
</table>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Race result variant 1 — standard IRC result table
# ---------------------------------------------------------------------------

RACE_STANDARD = """<!DOCTYPE html>
<html>
<head><title>Rating Gold Race 1</title></head>
<body>
<p class="heading1">Hamilton Island Race Week 2024</p>
<p class="boldTextBlue">Race 1</p>
<p>Date: 17/08/2024</p>
<table class="centre_results_table">
  <caption>Division 1  IRC results  Start : 11:35</caption>
  <tr>
    <td>Place</td><td>Sail No</td><td>Boat Name</td><td>Skipper</td>
    <td>Fin Tim</td><td>Elapsd</td><td>AHC</td><td>Cor'd T</td><td>BCH</td>
  </tr>
  <tr>
    <td>1</td><td>AUS001</td><td>Black Jack</td><td>M. Bradford</td>
    <td>14:22:10</td><td>2:47:10</td><td>1.105</td><td>3:04:36</td><td>1.108</td>
  </tr>
  <tr>
    <td>2</td><td>AUS002</td><td>Alive</td><td>D. Chapman</td>
    <td>14:31:05</td><td>2:56:05</td><td>1.089</td><td>3:11:47</td><td>1.092</td>
  </tr>
  <tr>
    <td>3</td><td>AUS003</td><td>Celestial</td><td>S. Haynes</td>
    <td>14:40:44</td><td>3:05:44</td><td>1.061</td><td>3:16:59</td><td>1.064</td>
  </tr>
</table>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Race result variant 2 — DNF / RET / DNS status codes
# ---------------------------------------------------------------------------

RACE_DNF = """<!DOCTYPE html>
<html>
<head><title>Rating Gold Race 2</title></head>
<body>
<p class="heading1">Hamilton Island Race Week 2024</p>
<p class="boldTextBlue">Race 2</p>
<p>Date: 18/08/2024</p>
<table class="centre_results_table">
  <caption>Division 1  IRC results  Start : 11:35</caption>
  <tr>
    <td>Place</td><td>Sail No</td><td>Boat Name</td><td>Skipper</td>
    <td>Fin Tim</td><td>Elapsd</td><td>AHC</td><td>Cor'd T</td><td>BCH</td>
  </tr>
  <tr>
    <td>1</td><td>AUS001</td><td>Black Jack</td><td>M. Bradford</td>
    <td>13:58:01</td><td>2:23:01</td><td>1.105</td><td>2:38:14</td><td>1.108</td>
  </tr>
  <tr>
    <td>DNF</td><td>AUS002</td><td>Alive</td><td>D. Chapman</td>
    <td>&nbsp;</td><td>&nbsp;</td><td>1.089</td><td>&nbsp;</td><td>1.092</td>
  </tr>
  <tr>
    <td>DNS</td><td>AUS003</td><td>Celestial</td><td>S. Haynes</td>
    <td>&nbsp;</td><td>&nbsp;</td><td>1.061</td><td>&nbsp;</td><td>1.064</td>
  </tr>
</table>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Race result variant 3 — multi-division page (only IRC tables parsed)
# ---------------------------------------------------------------------------

RACE_MULTICLASS = """<!DOCTYPE html>
<html>
<head><title>Regatta Multi-Division Race 1</title></head>
<body>
<p class="heading1">Regatta Multi-Division</p>
<p class="boldTextBlue">Race 1</p>
<p>Date: 17/08/2024</p>
<table class="centre_results_table">
  <caption>Division 1  PHS results  Start : 11:35</caption>
  <tr>
    <td>Place</td><td>Sail No</td><td>Boat Name</td><td>Skipper</td>
    <td>Fin Tim</td><td>Elapsd</td><td>AHC</td><td>Cor'd T</td>
  </tr>
  <tr>
    <td>1</td><td>P001</td><td>Cruiser One</td><td>A. Smith</td>
    <td>14:00:00</td><td>2:25:00</td><td>0.950</td><td>2:17:45</td>
  </tr>
</table>
<table class="centre_results_table">
  <caption>Division 2  IRC results  Start : 11:40</caption>
  <tr>
    <td>Place</td><td>Sail No</td><td>Boat Name</td><td>Skipper</td>
    <td>Fin Tim</td><td>Elapsd</td><td>AHC</td><td>Cor'd T</td>
  </tr>
  <tr>
    <td>1</td><td>AUS101</td><td>Racer X</td><td>J. Doe</td>
    <td>14:10:00</td><td>2:30:00</td><td>1.020</td><td>2:33:00</td>
  </tr>
  <tr>
    <td>2</td><td>AUS102</td><td>Racer Y</td><td>K. Roe</td>
    <td>14:12:00</td><td>2:32:00</td><td>1.015</td><td>2:34:17</td>
  </tr>
</table>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Race result variant 4 — PHS-only page (parser must emit zero records)
# ---------------------------------------------------------------------------

RACE_NO_IRC = """<!DOCTYPE html>
<html>
<head><title>Passage PHS Race 1</title></head>
<body>
<p class="heading1">Passage Series</p>
<p class="boldTextBlue">Race 1</p>
<p>Date: 17/08/2024</p>
<table class="centre_results_table">
  <caption>Passage  PHS results  Start : 10:00</caption>
  <tr>
    <td>Place</td><td>Sail No</td><td>Boat Name</td><td>Skipper</td>
    <td>Fin Tim</td><td>Elapsd</td><td>AHC</td><td>Cor'd T</td>
  </tr>
  <tr>
    <td>1</td><td>P777</td><td>Slow Boat</td><td>B. Cruise</td>
    <td>13:00:00</td><td>3:00:00</td><td>0.900</td><td>2:42:00</td>
  </tr>
</table>
</body>
</html>
"""

# A "changed" version of the standard race (one extra finisher) used to
# prove incremental reruns fetch *changed* material.
RACE_STANDARD_V2 = RACE_STANDARD.replace(
    "</table>",
    """  <tr>
    <td>4</td><td>AUS004</td><td>Patriot</td><td>J. Holder</td>
    <td>14:55:00</td><td>3:20:00</td><td>1.050</td><td>3:29:50</td><td>1.052</td>
  </tr>
</table>""",
)

# ---------------------------------------------------------------------------
# Breakage mutations — source-breakage detection tests
# ---------------------------------------------------------------------------

# Mutation A: the results table is removed entirely (structural breakage).
MUTATED_NO_TABLES = """<!DOCTYPE html>
<html>
<head><title>Rating Gold Race 1</title></head>
<body>
<p class="heading1">Hamilton Island Race Week 2024</p>
<p class="boldTextBlue">Race 1</p>
<p>Results temporarily unavailable — site redesign in progress.</p>
</body>
</html>
"""

# Mutation B: the boat-name column header is renamed so the parser can no
# longer identify boats (semantic breakage — must yield zero records, not
# garbage).
MUTATED_HEADERS_RENAMED = """<!DOCTYPE html>
<html>
<head><title>Rating Gold Race 1</title></head>
<body>
<p class="heading1">Hamilton Island Race Week 2024</p>
<p class="boldTextBlue">Race 1</p>
<p>Date: 17/08/2024</p>
<table class="centre_results_table">
  <caption>Division 1  IRC results  Start : 11:35</caption>
  <tr>
    <td>Pos</td><td>Sail#</td><td>YachtName</td><td>Driver</td>
    <td>Clock</td><td>ET</td><td>RatingFactor</td><td>CorrTime</td>
  </tr>
  <tr>
    <td>1</td><td>AUS001</td><td>Black Jack</td><td>M. Bradford</td>
    <td>14:22:10</td><td>2:47:10</td><td>1.105</td><td>3:04:36</td>
  </tr>
</table>
</body>
</html>
"""

# Mutation C: a wholly unrelated page (no result content at all).
MUTATED_IRRELEVANT = """<!DOCTYPE html>
<html>
<head><title>Club Notice Board</title></head>
<body>
<h1>Club Notice Board</h1>
<p>The bar opens at 5pm on Fridays.  Working bee next Saturday.</p>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Fixture registry — path → (body, content_type)
# ---------------------------------------------------------------------------

#: Base path prefix used by the in-test mock server.
BASE = "/results"


def fixture_routes(year: int = 2024, division: str = "hirw") -> dict[str, str]:
    """Return the canonical route map for a healthy source tree.

    Keys are URL paths; values are raw HTML bodies.
    """
    return {
        f"{BASE}/{year}/{division}/index.htm": INDEX_HTM,
        f"{BASE}/{year}/{division}/rategold/series.htm": SERIES_HTM,
        f"{BASE}/{year}/{division}/pass/series.htm": SERIES_HTM_B,
        f"{BASE}/{year}/{division}/rategold/01RGrp2.htm": RACE_STANDARD,
        f"{BASE}/{year}/{division}/rategold/02RGrp2.htm": RACE_DNF,
        f"{BASE}/{year}/{division}/pass/01RGrp2.htm": RACE_MULTICLASS,
    }

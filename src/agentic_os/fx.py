"""USD -> EUR exchange rate from the European Central Bank daily reference rates.

The ECB publishes one free, key-less XML file per working day. The rate is only
used to show costs in euros, so a manual fallback rate is enough when the fetch
fails (no network, weekend before the first publication, format change).
"""

from __future__ import annotations

import asyncio
import re
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Literal

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
DEFAULT_EUR_PER_USD = 0.86

_TIME = re.compile(r"""time=['"](\d{4}-\d{2}-\d{2})['"]""")
_USD = re.compile(r"""currency=['"]USD['"]\s+rate=['"]([0-9]+(?:\.[0-9]+)?)['"]""")


@dataclass(frozen=True, slots=True)
class FxRate:
    eur_per_usd: float
    as_of: date | None
    source: Literal["ecb", "manual"]

    def to_wire(self) -> dict[str, object]:
        return {
            "eur_per_usd": self.eur_per_usd,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "source": self.source,
        }


class FxError(Exception):
    pass


def parse_ecb_daily(document: str) -> FxRate:
    """Parse the ECB daily XML (1 EUR = N USD) into EUR per USD."""
    time_match = _TIME.search(document)
    usd_match = _USD.search(document)
    if not time_match or not usd_match:
        raise FxError("Format inesperat del fitxer del BCE.")
    usd_per_eur = float(usd_match.group(1))
    if not 0.2 < usd_per_eur < 5:
        raise FxError(f"Tipus de canvi USD fora de rang: {usd_per_eur}.")
    return FxRate(
        eur_per_usd=round(1 / usd_per_eur, 6),
        as_of=date.fromisoformat(time_match.group(1)),
        source="ecb",
    )


def _download(url: str, timeout: float) -> str:
    if not url.startswith("https://"):
        raise FxError("Només s'accepten URL https.")
    request = urllib.request.Request(url, headers={"User-Agent": "agentic-os"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body: bytes = response.read(1_000_000)
    return body.decode("utf-8", errors="replace")


async def fetch_ecb_rate(url: str = ECB_DAILY_URL, timeout: float = 10.0) -> FxRate:
    try:
        document = await asyncio.to_thread(_download, url, timeout)
    except OSError as exc:
        raise FxError(f"No s'ha pogut obtenir el tipus de canvi del BCE: {exc}") from exc
    return parse_ecb_daily(document)


def manual_rate(eur_per_usd: float = DEFAULT_EUR_PER_USD) -> FxRate:
    return FxRate(eur_per_usd=eur_per_usd, as_of=None, source="manual")

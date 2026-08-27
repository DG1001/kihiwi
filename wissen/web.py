"""Websuche über das lokale SearXNG.

**Das ist die einzige Stelle, an der etwas das Netz verlässt.** Die stehende
Auflage des Projekts lautet sonst: nichts verlässt das Netz. Fred hat die
Websuche ausdrücklich gewollt; sie ist deshalb einzeln abschaltbar
(`konfig.WEB_SUCHE`), und der Assistent sagt an, wenn er sie benutzt hat.

Zu bedenken: die Suchanfrage enthält, wonach im Labor gefragt wurde. Sie geht
über SearXNG an fremde Suchmaschinen. Nichts vom Audio oder von den Unterlagen
verlässt die Maschine -- wohl aber die Frage.
"""
from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request

from sprachdienst import konfig


def _ruf(frage: str, anzahl: int, timeout: float):
    url = (konfig.SEARXNG + "/search?" +
           urllib.parse.urlencode({"q": frage, "format": "json",
                                   "language": "de", "safesearch": "0"}))
    with urllib.request.urlopen(url, timeout=timeout) as r:
        d = json.load(r)
    aus = []
    for t in d.get("results", [])[:anzahl]:
        aus.append({"titel": (t.get("title") or "").strip(),
                    "text": (t.get("content") or "").strip()[:400],
                    "url": t.get("url", "")})
    return aus


async def suchen(frage: str, anzahl: int = 4, timeout: float = 12.0) -> list[dict]:
    if not konfig.WEB_SUCHE:
        return []
    try:
        return await asyncio.to_thread(_ruf, frage, anzahl, timeout)
    except Exception:
        return []

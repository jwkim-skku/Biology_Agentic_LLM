from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


ENSEMBL_REST_BASE_URL = "https://rest.ensembl.org"
SPECIES_ALIASES = {
    "human": "homo_sapiens",
    "homo sapiens": "homo_sapiens",
    "homo_sapiens": "homo_sapiens",
    "mouse": "mus_musculus",
    "mus musculus": "mus_musculus",
    "mus_musculus": "mus_musculus",
}


class EnsemblClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class EnsemblClient:
    base_url: str = ENSEMBL_REST_BASE_URL
    timeout_seconds: int = 20

    def lookup_symbol(self, species: str, symbol: str, *, include_mane: bool = True) -> dict[str, Any]:
        normalized_species = normalize_species(species)
        mane_flag = ";mane=1" if include_mane and normalized_species == "homo_sapiens" else ""
        path = f"/lookup/symbol/{normalized_species}/{quote(symbol.upper())}?expand=1{mane_flag}"
        return self._get_json(path)

    def fetch_cds(self, transcript_id: str) -> str:
        stable_id = strip_ensembl_version(transcript_id)
        path = f"/sequence/id/{quote(stable_id)}?type=cds"
        return self._get_text(path).replace("\n", "").strip().upper()

    def _get_json(self, path: str) -> dict[str, Any]:
        response_text = self._request(path, content_type="application/json")
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise EnsemblClientError("Ensembl returned non-JSON data.") from exc
        if not isinstance(payload, dict):
            raise EnsemblClientError("Ensembl returned an unexpected JSON payload.")
        return payload

    def _get_text(self, path: str) -> str:
        return self._request(path, content_type="text/plain")

    def _request(self, path: str, *, content_type: str) -> str:
        url = f"{self.base_url}{path}"
        request = Request(url, headers={"Content-Type": content_type, "User-Agent": "codon-optimizer-mvp/0.1"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise EnsemblClientError(f"Ensembl request failed with HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise EnsemblClientError(f"Could not connect to Ensembl: {exc.reason}") from exc


def normalize_species(species: str) -> str:
    normalized = species.strip().lower().replace("-", " ").replace("_", " ")
    if normalized in SPECIES_ALIASES:
        return SPECIES_ALIASES[normalized]
    underscore = normalized.replace(" ", "_")
    if underscore in SPECIES_ALIASES:
        return SPECIES_ALIASES[underscore]
    return underscore


def strip_ensembl_version(identifier: str) -> str:
    return identifier.split(".", 1)[0]


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()

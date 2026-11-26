"""Turn a playbook's forwarder list (GUIDs, hostnames or labels) into forwarder GUIDs."""

import re
from dataclasses import dataclass

from soar_sdk.exceptions import ActionFailure

from .client import PromotionClient

MATCH_ORDER = ("guid", "hostname", "label")


@dataclass(frozen=True)
class Resolved:
    value: str
    guid: str
    hostname: str


def split_values(spec: str | None, rows: list[dict] | None = None) -> list[str]:
    """Split on commas, semicolons and newlines; a piece that is not a forwarder as a whole
    (a label may contain spaces) is split again on spaces."""
    values = []
    for raw in re.split(r"[,;\n]+", spec or ""):
        piece = raw.strip()
        if not piece:
            continue
        if " " in piece and not (rows and _match(piece, rows)):
            values += piece.split()
        else:
            values.append(piece)
    return values


def _match(value: str, rows: list[dict]) -> list[dict]:
    """Rows of the first key (GUID, hostname, label) that matches the value, ignoring case."""
    wanted = value.casefold()
    for key in MATCH_ORDER:
        hits = [r for r in rows if str(r.get(key) or "").casefold() == wanted]
        if hits:
            return hits
    return []


def resolve_forwarders(
    spec: str | None, rows: list[dict]
) -> tuple[list[str], list[Resolved]]:
    """Return (GUIDs to send, what each value resolved to). '*' alone means every forwarder.

    Fails, naming the values, when the list is empty, mixes '*' with names, or a value
    matches no forwarder or more than one. It never widens to more forwarders than asked.
    """
    values = split_values(spec, rows)
    if not values:
        raise ActionFailure(
            "forwarders is required: give GUIDs or hostnames, or * for every forwarder"
        )
    if "*" in values:
        if len(values) > 1:
            raise ActionFailure(
                "forwarders: use * alone for every forwarder, or list GUIDs or hostnames"
            )
        return ["*"], []

    resolved: list[Resolved] = []
    unknown: list[str] = []
    ambiguous: list[str] = []
    for value in values:
        hits = _match(value, rows)
        guids = sorted({str(r["guid"]).upper() for r in hits})
        if not guids:
            unknown.append(value)
        elif len(guids) > 1:
            ambiguous.append(f"{value} ({', '.join(guids)})")
        else:
            resolved.append(
                Resolved(value, guids[0], str(hits[0].get("hostname") or ""))
            )

    problems = []
    if unknown:
        problems.append("no forwarder named " + ", ".join(unknown))
    if ambiguous:
        problems.append(
            "more than one forwarder matches "
            + "; ".join(ambiguous)
            + " — use the GUID"
        )
    if problems:
        raise ActionFailure("forwarders: " + "; ".join(problems))

    return list(dict.fromkeys(r.guid for r in resolved)), resolved


def resolve_with_client(
    client: PromotionClient, spec: str | None
) -> tuple[list[str], list[Resolved]]:
    """Resolve against every forwarder the Search Head knows (not only those holding a
    sourcetype, so the API reports a host without the data as such)."""
    values = split_values(spec)
    if values == ["*"]:
        return ["*"], []
    if not values:
        return resolve_forwarders(spec, [])
    return resolve_forwarders(spec, client.get("forwarders").get("items", []))

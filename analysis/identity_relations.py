"""Conservative, evidence-checked identity unions for normalized export libraries.

No relations file means no cross-ID or cross-package mobile unions. This module
never treats a name, a single difficulty, or an ID arithmetic rule as identity.
"""
from __future__ import annotations

import collections
from decimal import Decimal, InvalidOperation
import unicodedata

DIFFICULTIES = ("BSC", "ADV", "EXT")


class Union:
    def __init__(self):
        self.parents = {}

    def get(self, key):
        self.parents.setdefault(key, key)
        if self.parents[key] != key:
            self.parents[key] = self.get(self.parents[key])
        return self.parents[key]

    def join(self, left, right):
        self.parents[self.get(right)] = self.get(left)


def identity_key(version, song_id, platform):
    return ("arcade:" if platform == "arcade" else version + ":") + str(song_id)


def bundle(observation):
    charts = observation.get("charts", {})
    if not all(charts.get(d, {}).get("playable") and
               charts[d].get("gameplay_sha256") for d in DIFFICULTIES):
        return None
    return tuple(charts[d]["gameplay_sha256"] for d in DIFFICULTIES)


def dual_audio(observation):
    audio = observation.get("audio", {})
    values = tuple(audio.get(k, {}).get("source_sha256") for k in ("bgm", "preview"))
    return values if all(values) else None


def title_artist(observation):
    def norm(value):
        return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())
    key = (norm(observation.get("title")), norm(observation.get("artist")))
    return key if all(key) else None


def resolve_identities(observations, relations=None, alias_audio_audit=None, *, cover_hash=None):
    """Return a union plus accepted/rejected evidence without opening audio.

    Supports the explicit audited relation schema described in normalized-library.md.
    Every admitted relation is checked again against the current export observations.
    An invalid/stale/ambiguous relation is reported and left unmerged.
    The optional cover_hash callback reads only explicitly named cover candidates.
    """
    union = Union()
    bykey = collections.defaultdict(list)
    for observation in observations:
        bykey[observation["key"]].append(observation)
        union.get(observation["key"])
    accepted, rejected = [], []
    relations = relations or {}
    audit = alias_audio_audit or {}

    def admit(kind, left, right, valid, reason, details=None):
        row = {"kind": kind, "left": left, "right": right, "reason": reason}
        if details is not None:
            row["evidence"] = details
        if valid and left in bykey and right in bykey:
            union.join(left, right)
            accepted.append(row)
        else:
            rejected.append(row)

    def endpoint(value):
        return str(value["version"]) + ":" + str(value["id"])

    def independent_official(observation):
        return (observation.get("layer", "official") == "official" and
                observation.get("kind", "release_package") == "release_package")

    def signatures(key, official_arcade=False):
        return {b for o in bykey.get(key, []) if
                (not official_arcade or independent_official(o)) and (b := bundle(o))}

    def same_dual_bundle(left, right):
        def values(key):
            return {(b, a) for o in bykey.get(key, [])
                    if (b := bundle(o)) and (a := dual_audio(o))}
        return bool(values(left) & values(right))

    # A chart-content alias alone is insufficient to merge two arcade IDs.
    # Require the audit's explicit approval AND a connected current three-chart /
    # original BGM+preview identity graph. A rejected group remains separate.
    for group in audit.get("arcade_alias_group_audio_evidence", []):
        keys = ["arcade:" + str(sid) for sid in group.get("arcade_ids", [])]
        if not keys:
            continue
        reachable = {keys[0]}
        while True:
            more = {b for a in reachable for b in keys if same_dual_bundle(a, b)}
            if more <= reachable:
                break
            reachable |= more
        valid = (bool(group.get("all_ids_connected_by_identical_dual_audio")) and
                 set(keys) <= reachable)
        for key in keys[1:]:
            admit("arcade_dual_audio_and_three_chart_alias", keys[0], key, valid,
                  "audit approval plus current connected identical BGM/preview/three-chart evidence")

    # An explicit independently inspected cover/reading relation can admit an
    # identity with different audio payloads. Hash only its named cover candidates.
    for group in relations.get("arcade_cover_reading_alias_groups", []):
        keys = ["arcade:" + str(sid) for sid in group.get("arcade_ids", [])]
        reading, expected = group.get("name_reading_cp932"), group.get("cover_sha256")
        per_id = group.get("cover_sha256_by_id")
        certificates = group.get("observations", [])
        visual_valid = (per_id is None or (isinstance(per_id, dict) and group.get("title_visual_verified") is True and
                        isinstance(group.get("title"), str) and bool(group["title"].strip()) and
                        bool(certificates)))

        def metadata_signature(metadata):
            def number(value):
                try:
                    result = Decimal(str(value))
                    return str(result.normalize()) if result.is_finite() else None
                except (InvalidOperation, ValueError):
                    return None
            maximum = number(metadata.get("bpm_max"))
            levels = tuple(number(metadata.get("detail_level_" + d.lower(),
                                         metadata.get("level_" + d.lower()))) for d in DIFFICULTIES)
            if maximum is None or any(level is None for level in levels):
                return None
            return (number(metadata.get("bpm_min")), maximum, *levels)

        def attested(observation, sid, expected_hash):
            if not certificates:
                return not per_id
            return any(str(certificate.get("id")) == sid and
                       certificate.get("version", observation.get("version")) == observation.get("version") and
                       certificate.get("layer") == observation.get("layer", "official") and
                       certificate.get("manifest") == observation.get("source_manifest") and
                       (not per_id or certificate.get("cover_sha256") == expected_hash)
                       for certificate in certificates)

        common = None
        for key in keys:
            sid = key.split(":", 1)[1]
            expected_hash = per_id.get(sid) if isinstance(per_id, dict) else expected
            eligible = set()
            for observation in bykey.get(key, []):
                metadata = observation.get("metadata") or {}
                signature = bundle(observation)
                if (isinstance(metadata, dict) and visual_valid and reading and expected_hash and signature and cover_hash and
                        attested(observation, sid, expected_hash) and
                        metadata.get("name_reading_cp932") == reading and observation.get("cover") and
                        cover_hash(observation) == expected_hash):
                    if per_id:
                        musical_metadata = metadata_signature(metadata)
                        if musical_metadata is not None:
                            eligible.add((signature, musical_metadata))
                    else:
                        eligible.add(signature)
            common = eligible if common is None else common & eligible
        valid = len(set(keys)) == len(keys) and len(keys) > 1 and bool(common)
        for key in keys[1:]:
            admit("explicit_arcade_cover_reading_three_chart_alias", keys[0], key, valid,
                  "explicit relation plus current reading/PNG/three-chart evidence; different PNGs additionally require bound visual-title certificates and equal BPM/levels; audio may differ",
                  dict(name_reading_cp932=reading, cover_sha256=expected,
                       cover_sha256_by_id=per_id, title=group.get("title"),
                       title_visual_verified=bool(group.get("title_visual_verified")),
                       shared_gameplay_hashes=[list(s[0] if per_id else s) for s in sorted(common or (), key=repr)]))

    arcade_candidates = collections.defaultdict(set)
    for key in bykey:
        if key.startswith("arcade:"):
            for signature in signatures(key, official_arcade=True):
                arcade_candidates[signature].add(key)

    # Single-difficulty matches from a comparison report are deliberately ignored.
    edges = [(e, str(e.get("arcade_id", ""))) for e in
             relations.get("unique_three_chart_relations", [])]
    edges += [(e, str((e.get("arcade_ids") or [""])[0])) for e in
              relations.get("mobile_relations_resolved_via_verified_arcade_alias_group", [])]
    for edge, arcade_id in edges:
        left = "arcade:" + arcade_id
        right = str(edge.get("mobile_version", "")) + ":" + str(edge.get("mobile_id", ""))
        common = signatures(left, official_arcade=True) & signatures(right)
        declared = edge.get("gameplay_hashes")
        if declared:
            common &= {tuple(declared.get(d) for d in DIFFICULTIES)}
        valid = any({union.get(k) for k in arcade_candidates[sig]} == {union.get(left)}
                    for sig in common)
        admit("coherent_three_playable_charts", left, right, valid,
              "current three-chart signature must match exactly one independent official arcade component; mod/prototype/update/merged candidates excluded")

    for edge in relations.get("plus_jukebeat_same_content_relations", []):
        left, right = endpoint(edge["left"]), endpoint(edge["right"])
        admit("mobile_identical_three_charts_and_dual_audio", left, right,
              same_dual_bundle(left, right), "current three-chart and original BGM/preview identity")

    def same_metadata_bgm(left, right):
        for a in bykey.get(left, []):
            for b in bykey.get(right, []):
                key = title_artist(a)
                ah = a.get("audio", {}).get("bgm", {}).get("source_sha256")
                bh = b.get("audio", {}).get("bgm", {}).get("source_sha256")
                if key and key == title_artist(b) and ah and ah == bh:
                    return True
        return False

    for edge in relations.get("strict_title_artist_and_bgm_identity_edges", []):
        left, right = endpoint(edge["left"]), endpoint(edge["right"])
        admit("exact_title_artist_and_original_bgm", left, right,
              same_metadata_bgm(left, right),
              "current nonempty normalized title+artist AND original BGM SHA; chart variants retained")

    for edge in relations.get("title_artist_bgm_anchored_variant_relations", []):
        left = "arcade:" + str(edge.get("arcade_id_candidate", ""))
        right = str(edge.get("version", "")) + ":" + str(edge.get("id", ""))
        anchors = [endpoint(a) for a in edge.get("identical_original_bgm_to_anchor", [])]
        valid = any(anchor in bykey and union.get(anchor) == union.get(left) and
                    same_metadata_bgm(anchor, right) for anchor in anchors)
        admit("metadata_bgm_anchored_chart_variant", left, right, valid,
              "a previously admitted anchor has the same current title/artist/original BGM")

    return union, {"schema": 1, "mode": "explicit_evidence" if relations or audit else
                   "namespace_id_only", "accepted": accepted, "rejected": rejected,
                   "policy": "No inferred numeric ID mapping or name-only merges; distinct gameplay bundles remain separate."}

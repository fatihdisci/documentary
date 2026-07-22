"""Turning a selection into cuts.

The whole problem this module solves: transitions **overlap** two sections, so
the frames between ``entry[i].end - d`` and ``entry[i].end`` belong to both. Cut
each selected section as its own independent clip and a non-contiguous selection
carries half a dissolve from a section the user did not pick — which reads as a
duplicate frame or a flicker.

The rule implemented here:

* Sections the user selected back-to-back, with no trim at the join, merge into
  **one contiguous group**. The group is cut out of the finished video in a
  single span, so the transition, subtitles, narration and music between them
  survive exactly as they were mixed — nothing is re-assembled.
* Group boundaries are clamped to the manifest's safe range, which sits outside
  any transition shared with an unselected neighbour.
* Nothing is added between groups. No fade, no dip, no effect that the source
  does not already contain. ``ShortLayout.group_gap_fade_seconds`` exists as the
  extension point and is locked to zero.
"""

from __future__ import annotations

import hashlib
import json
import logging

from app.errors import ErrorCode, ValidationError
from app.shorts.captions import CAPTION_RENDERER_VERSION
from app.shorts.encode import encoder_fingerprint
from app.shorts.manifest import ManifestEntry, RenderManifest
from app.shorts.models import (
    MAX_SHORT_SECONDS,
    MIN_CLIP_SECONDS,
    RECOMMENDED_MAX_SECONDS,
    RECOMMENDED_MIN_SECONDS,
    ShortCaptionMode,
    ShortCaptionStyle,
    ShortGroupPlan,
    ShortLayout,
    ShortPlan,
    ShortRequest,
    ShortSegmentPlan,
    ShortSegmentRequest,
    resolve_caption_style,
)

logger = logging.getLogger("evb.shorts.plan")

#: Times are compared at millisecond resolution. Below that, a "trim" is the
#: user's slider rounding, not an intent to cut.
EPSILON = 0.0015


def build_plan(manifest: RenderManifest, request: ShortRequest) -> ShortPlan:
    """Resolve, validate and group a selection. Raises on anything unusable."""
    segments = _resolve_segments(manifest, request.segments)
    groups = _group(manifest, segments)

    total = round(sum(group.duration_seconds for group in groups), 4)
    if total > MAX_SHORT_SECONDS + EPSILON:
        raise ValidationError(
            ErrorCode.SHORT_TOO_LONG,
            f"Seçiminiz {total:.1f} saniye. YouTube kısa videoları için sınır "
            f"{MAX_SHORT_SECONDS:.0f} saniye.",
            details=(
                f"{len(segments)} bölüm seçildi, "
                f"{len(groups)} parça, toplam {total:.3f} sn"
            ),
        )

    warnings = _warnings(total, groups)
    plan = ShortPlan(
        segments=segments,
        groups=groups,
        total_duration_seconds=total,
        warnings=warnings,
    )
    plan.cache_key = cache_key(
        manifest,
        plan,
        request.layout,
        caption_mode=request.caption_mode,
        caption_style=request.resolved_caption_style(),
    )
    return plan


def _resolve_segments(
    manifest: RenderManifest, requested: list[ShortSegmentRequest]
) -> list[ShortSegmentPlan]:
    if not requested:
        raise ValidationError(
            ErrorCode.SHORT_INVALID_SELECTION,
            "Kısa video için en az bir bölüm seçin.",
            details="hiç bölüm seçilmemiş",
        )

    seen: set[str] = set()
    resolved: list[ShortSegmentPlan] = []

    for position, item in enumerate(requested):
        entry = manifest.entry(item.unit_id)
        if entry is None:
            raise ValidationError(
                ErrorCode.SHORT_INVALID_SELECTION,
                f"'{item.unit_id}' bölümü seçtiğiniz videoda yok.",
                details=(
                    "bu videodaki bölümler: "
                    + ", ".join(f"{e.number} ({e.unit_id})" for e in manifest.entries)
                ),
                unit_id=item.unit_id,
            )
        if item.unit_id in seen:
            raise ValidationError(
                ErrorCode.SHORT_INVALID_SELECTION,
                f"{entry.number}. bölüm — {entry.title} iki kez seçilmiş.",
                details=f"{position + 1}. sırada '{item.unit_id}' tekrar ediyor",
                suggestion="Tekrarı kaldırın. Her bölüm bir kısa videoda bir kez yer alabilir.",
                unit_id=item.unit_id,
            )
        seen.add(item.unit_id)
        resolved.append(_resolve_one(entry, item))

    return resolved


def _resolve_one(entry: ManifestEntry, item: ShortSegmentRequest) -> ShortSegmentPlan:
    """Apply the requested trim, refusing anything outside the safe range."""
    safe_start = entry.safe_start_seconds
    safe_end = entry.safe_end_seconds
    label = f"{entry.number}. bölüm — {entry.title}"

    start = safe_start if item.start_seconds is None else float(item.start_seconds)
    end = safe_end if item.end_seconds is None else float(item.end_seconds)

    if start < safe_start - EPSILON or end > safe_end + EPSILON:
        raise ValidationError(
            ErrorCode.SHORT_INVALID_TRIM,
            f"{label} yalnızca {safe_start:.2f} sn ile {safe_end:.2f} sn arasında kırpılabilir.",
            details=(
                f"istenen: {start:.3f} sn – {end:.3f} sn\n"
                f"izin verilen: {safe_start:.3f} sn – {safe_end:.3f} sn\n"
                "dışarıda bırakılan kısımlar, bu bölümün komşularıyla paylaştığı geçişlerdir; "
                "oradan kesmek görüntüde tekrara yol açar"
            ),
            unit_id=entry.unit_id,
        )

    # Snap back inside after the epsilon check, so float noise never leaks into
    # an FFmpeg timestamp.
    start = min(max(start, safe_start), safe_end)
    end = min(max(end, safe_start), safe_end)

    if end - start < MIN_CLIP_SECONDS - EPSILON:
        raise ValidationError(
            ErrorCode.SHORT_INVALID_TRIM,
            f"{label} {max(0.0, end - start):.2f} saniye olurdu; en az "
            f"{MIN_CLIP_SECONDS:.1f} saniye olmalı.",
            details=f"başlangıç {start:.3f} sn, bitiş {end:.3f} sn",
            suggestion=(
                "Kırpmayı genişletin ya da bu bölümü seçimden çıkarın. "
                f"{MIN_CLIP_SECONDS:.1f} saniyeden kısa bir parça, sahne gibi değil hata gibi "
                "görünür."
            ),
            unit_id=entry.unit_id,
        )

    trimmed = abs(start - safe_start) > EPSILON or abs(end - safe_end) > EPSILON
    return ShortSegmentPlan(
        unit_id=entry.unit_id,
        number=entry.number,
        title=entry.title,
        kind=entry.kind,
        start_seconds=round(start, 4),
        end_seconds=round(end, 4),
        duration_seconds=round(end - start, 4),
        trimmed=trimmed,
    )


def _group(manifest: RenderManifest, segments: list[ShortSegmentPlan]) -> list[ShortGroupPlan]:
    """Merge back-to-back selections into single contiguous cuts."""
    order = {entry.unit_id: index for index, entry in enumerate(manifest.entries)}
    groups: list[ShortGroupPlan] = []
    previous: ShortSegmentPlan | None = None

    for segment in segments:
        if previous is not None and _mergeable(manifest, order, previous, segment):
            group = groups[-1]
            group.end_seconds = segment.end_seconds
            group.duration_seconds = round(group.end_seconds - group.start_seconds, 4)
            group.unit_ids.append(segment.unit_id)
            group.numbers.append(segment.number)
            group.preserved_transitions += 1
        else:
            groups.append(
                ShortGroupPlan(
                    index=len(groups),
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    duration_seconds=segment.duration_seconds,
                    unit_ids=[segment.unit_id],
                    numbers=[segment.number],
                )
            )
        segment.group_index = len(groups) - 1
        previous = segment

    return groups


def _mergeable(
    manifest: RenderManifest,
    order: dict[str, int],
    previous: ShortSegmentPlan,
    current: ShortSegmentPlan,
) -> bool:
    """Whether two selections can be cut as one span.

    Three conditions, all necessary:

    1. they are neighbours on the source timeline, in that order;
    2. the earlier one runs to its safe end — no trim at the join;
    3. the later one starts at its safe start — likewise.

    When any trim touches the join, the two stay separate cuts. That drops the
    transition frames between them, which is correct: a hard cut is honest, half
    a dissolve played twice is not.
    """
    if order.get(current.unit_id, -1) != order.get(previous.unit_id, -2) + 1:
        return False

    previous_entry = manifest.entry(previous.unit_id)
    current_entry = manifest.entry(current.unit_id)
    if previous_entry is None or current_entry is None:
        return False

    joined_at_end = abs(previous.end_seconds - previous_entry.safe_end_seconds) <= EPSILON
    joined_at_start = abs(current.start_seconds - current_entry.safe_start_seconds) <= EPSILON
    return joined_at_end and joined_at_start


def _warnings(total: float, groups: list[ShortGroupPlan]) -> list[str]:
    warnings: list[str] = []
    if total > MAX_SHORT_SECONDS:
        return warnings  # already a hard failure; no need to also nag

    if total < RECOMMENDED_MIN_SECONDS:
        warnings.append(
            f"Bu kısa video {total:.0f} saniye. Önerilen aralık "
            f"{RECOMMENDED_MIN_SECONDS:.0f}–{RECOMMENDED_MAX_SECONDS:.0f} saniye; daha kısa "
            "videolar izleyici ilgilenmeye fırsat bulamadan bitiyor."
        )
    elif total > RECOMMENDED_MAX_SECONDS:
        warnings.append(
            f"Bu kısa video {total:.0f} saniye; önerilen "
            f"{RECOMMENDED_MIN_SECONDS:.0f}–{RECOMMENDED_MAX_SECONDS:.0f} saniyelik aralığın "
            "üzerinde."
        )

    if total > 60.0:
        warnings.append(
            "60 saniyeyi aştı: bir dakikadan uzun kısa videolar, içindeki müzik telifliyse "
            "tüm dünyada engellenebilir. Müzik sizinse veya lisanslıysa sorun yok; değilse bir "
            "dakikanın altında kalın."
        )

    if len(groups) > 1:
        warnings.append(
            f"{len(groups)} ayrı parça: seçtiğiniz bölümler videoda yan yana olmadığı için "
            "doğrudan uç uca eklenecek. Hiçbir efekt eklenmez; ses ve görüntü bitmiş videodan "
            "olduğu gibi gelir."
        )
    return warnings


# --- cache key --------------------------------------------------------------


def cache_key(
    manifest: RenderManifest,
    plan: ShortPlan,
    layout: ShortLayout,
    *,
    caption_mode: ShortCaptionMode = ShortCaptionMode.SOURCE_BURNED_IN,
    caption_style: ShortCaptionStyle | None = None,
) -> str:
    """A deterministic content address for one Short.

    Two requests that would produce the same pixels produce the same key; any
    change to the source file, the cut points, the layout, the captions or the
    encoder produces a different one. That is what makes reuse safe: a cache hit
    can never serve a Short built from a video, a caption style or a cue list
    that has since changed.

    The ``captions`` block is added **only** when captions actually change the
    output — that is, in any mode other than the historical one. A request that
    does not mention captions therefore hashes to exactly the value it always
    did, and every Short already on disk keeps matching its own request.
    """
    payload: dict[str, object] = {
        "manifestSchema": manifest.schema_version,
        "sourceSha256": manifest.source.sha256,
        "encoder": encoder_fingerprint(manifest.profile.fps),
        "layout": {
            "width": layout.width,
            "height": layout.height,
            "background": layout.background_style.value,
            "style": layout.layout_style.value,
            "color": layout.background_color.upper(),
            "gapFade": round(layout.group_gap_fade_seconds, 4),
        },
        "groups": [
            {
                "start": round(group.start_seconds, 3),
                "end": round(group.end_seconds, 3),
                "units": list(group.unit_ids),
            }
            for group in plan.groups
        ],
    }

    if caption_mode is not ShortCaptionMode.SOURCE_BURNED_IN:
        package = manifest.shorts_source
        payload["captions"] = {
            "mode": caption_mode.value,
            "renderer": CAPTION_RENDERER_VERSION,
            # The picture itself comes from the clean master in these modes, so
            # its checksum belongs in the key even when nothing is drawn.
            "cleanMasterSha256": package.clean_master.sha256 if package else None,
            "cueSchema": package.cue_sidecar.schema_version if package else None,
            # The cue *content* hash, not the file hash: rewriting the side-car
            # with identical captions must not invalidate a finished Short.
            "cueContentHash": package.cue_sidecar.content_hash if package else None,
            "style": (
                resolve_caption_style(caption_style).model_dump(mode="json", by_alias=True)
                if caption_mode is ShortCaptionMode.SHORTS_NATIVE
                else None
            ),
        }

    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest[:16]

"""Phase 6 — 雙版本匯出。

- Creator Edition：creator 仲裁值（active Arbitration）；單一專家策展，不宣稱 ground truth。
- Dual-View Edition：industry / audience 並陳 + Phase 5 flags；audience N=1，single end-user reference。

legacy mean-consensus 的 build_dataset 留在 src/export.py 不動。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from src.annotation_serialization import annotation_to_dict
from src.arbitration import (
    ARBITRATED_FIELDS,
    bulk_load_arbitrations_by_audio,
    deserialize_value,
    latest_by_audio_field,
)
from src.audiofile_status import (
    bulk_load_annotations_by_audio,
    compute_status_from_preload,
    resolve_role_map,
)
from src.models import AudioFile
from src.quality_flags import audience_straight_lining
from src.role_gaps import classify_dim_flags, pairwise_gaps
from src.thresholds import HUMAN_CONTINUOUS_DIMS

EDITION_SCHEMA_VERSION = "1.0.0"
GENERATOR = "polan-annotator/phase6"
_ACOUSTIC = ("tonal_noise_ratio", "spectral_density")


def _envelope(edition: str, items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": EDITION_SCHEMA_VERSION,
        "edition": edition,
        "generated_at": datetime.now(UTC).isoformat(),
        "generator": GENERATOR,
        "total_items": len(items),
        "items": items,
        **extra,
    }


def build_creator_edition(session: Session) -> dict[str, Any]:
    """只收 creator_ready 檔；dimensions 取 active arbitration（非 mean）。"""
    role_map = resolve_role_map()
    anns_by_audio = bulk_load_annotations_by_audio(session)
    arbs_by_audio = bulk_load_arbitrations_by_audio(session)
    audios = session.exec(
        select(AudioFile).order_by(AudioFile.game_name, AudioFile.game_stage)
    ).all()

    items: list[dict[str, Any]] = []
    for audio in audios:
        anns = anns_by_audio.get(audio.id, [])
        arbs = arbs_by_audio.get(audio.id, [])
        if compute_status_from_preload(audio, anns, arbs, role_map) != "creator_ready":
            continue
        active = latest_by_audio_field(arbs)

        dims: dict[str, Any] = {}
        for d in HUMAN_CONTINUOUS_DIMS:
            rec = active[(audio.id, d)]
            dims[d] = deserialize_value(rec.arbitrated_value, rec.value_type)
        dims["tonal_noise_ratio"] = audio.tonal_noise_ratio_auto
        dims["spectral_density"] = audio.spectral_density_auto

        multi = {}
        for f in ("loop_capability", "source_type", "function_roles",
                  "genre_tag", "worldview_tag", "style_tag"):
            rec = active[(audio.id, f)]
            multi[f] = deserialize_value(rec.arbitrated_value, rec.value_type)

        arbitration_meta = {
            f: {
                "path": active[(audio.id, f)].path,
                "arbitrated_at": active[(audio.id, f)].arbitrated_at.isoformat(),
                "notes": active[(audio.id, f)].notes,
            }
            for f in ARBITRATED_FIELDS
        }
        dimension_sources = {d: "creator_arbitrated" for d in HUMAN_CONTINUOUS_DIMS}
        dimension_sources.update({a: "librosa_v1" for a in _ACOUSTIC})

        items.append({
            "audio_file": audio.filename,
            "audio_metadata": {
                "game_name": audio.game_name, "game_stage": audio.game_stage,
                "duration_sec": audio.duration_sec,
            },
            "dimensions": dims,
            "dimension_sources": dimension_sources,
            **multi,
            "arbitration_meta": arbitration_meta,
        })

    return _envelope("creator", items, dataset_name="polan_creator_edition_v1")


def build_dual_view(session: Session) -> dict[str, Any]:
    """獨立出貨軌，不需 creator：industry(yyslin) 標完即收。

    audience(Vic) 有標就帶 audience_view，沒標則 None（視情況加 Vic）。
    industry 軸目前單一標註員，非多人業界信度；creator 仲裁不在此版（見 meta）。
    """
    role_map = resolve_role_map()
    industry_id = role_map.get("industry")
    audience_id = role_map.get("audience")
    anns_by_audio = bulk_load_annotations_by_audio(session)
    audios = session.exec(
        select(AudioFile).order_by(AudioFile.game_name, AudioFile.game_stage)
    ).all()

    items: list[dict[str, Any]] = []
    all_audience_anns = []
    for audio in audios:
        by_id = {a.annotator_id: a for a in anns_by_audio.get(audio.id, [])}
        if audience_id in by_id:
            all_audience_anns.append(by_id[audience_id])
        if industry_id not in by_id:
            continue
        audience_ann = by_id.get(audience_id)
        # creator 刻意排除：Dual-View 不依賴 creator，creator_industry_gap 無意義
        by_role = {"creator": None, "industry": by_id[industry_id], "audience": audience_ann}
        flags = classify_dim_flags(pairwise_gaps(by_role))
        product_dims = sorted(d for d, fs in flags.items() if "product_divergence" in fs)

        items.append({
            "audio_file": audio.filename,
            "audio_metadata": {
                "game_name": audio.game_name, "game_stage": audio.game_stage,
                "duration_sec": audio.duration_sec,
            },
            "industry_view": _view(by_id[industry_id]),
            "audience_view": _view(audience_ann) if audience_ann is not None else None,
            "product_divergence_dims": product_dims,
        })

    return _envelope(
        "dual_view", items,
        dataset_name="polan_dual_view_v1",
        meta={
            "industry_n": 1,
            "audience_n": 1,
            "industry_disclaimer": (
                "industry axis is a single annotator (yyslin); "
                "not a multi-rater industry reliability estimate"
            ),
            "audience_disclaimer": "single end-user reference, not an audience distribution",
            "creator_note": (
                "creator-arbitrated values are exclusive to the creator (expert) edition; "
                "absent here by design"
            ),
        },
        audience_quality=audience_straight_lining(all_audience_anns),
    )


def export_readiness_summary(session: Session) -> dict[str, int]:
    """兩條出貨軌的可出貨筆數（dashboard 用）。

    - dual_view_shippable：industry(yyslin) 標完即可出（不需 creator）。
    - expert_shippable：creator_ready（creator 仲裁定案）。
    兩者可重疊（creator_ready 檔也算 dual_view_shippable）—— 是兩個獨立交付，非互斥。
    """
    role_map = resolve_role_map()
    industry_id = role_map.get("industry")
    anns_by_audio = bulk_load_annotations_by_audio(session)
    arbs_by_audio = bulk_load_arbitrations_by_audio(session)
    audios = session.exec(select(AudioFile)).all()

    dual = 0
    expert = 0
    for audio in audios:
        anns = anns_by_audio.get(audio.id, [])
        if industry_id is not None and any(a.annotator_id == industry_id for a in anns):
            dual += 1
        status = compute_status_from_preload(
            audio, anns, arbs_by_audio.get(audio.id, []), role_map
        )
        if status == "creator_ready":
            expert += 1
    return {"dual_view_shippable": dual, "expert_shippable": expert, "total": len(audios)}


def _view(ann) -> dict[str, Any]:
    d = annotation_to_dict(ann)
    keys = (*HUMAN_CONTINUOUS_DIMS, "loop_capability", "source_type",
            "function_roles", "genre_tag", "worldview_tag", "style_tag")
    return {k: d[k] for k in keys}

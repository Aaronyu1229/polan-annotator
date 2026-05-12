"""Phase 4 export aggregation 測試。

覆蓋 prompt 列出的 5 個必要 case + 3 個保險 case：
    1. single annotator → consensus_method == "single_annotator"
    2. 兩人連續維度 mean → 正確
    3. function_roles union
    4. source_type 平手 → null + warning
    5. is_complete=False 被排除
    6. loop_capability 三方各一票 → 0.5（離散 mode tie fallback）
    7. 空 DB → items=[]，不 crash
    8. individual.json 未知 annotator → 404（涵蓋 route 層）

fixture 模仿 tests/test_annotations_api.py：直接用 in-memory Session 寫 row，
function_roles / style_tag 用 json.dumps 存（對齊 POST /api/annotations 的真實格式）。
"""
from __future__ import annotations

import json

from sqlmodel import Session

from src.export import build_dataset
from src.models import Annotation, AudioFile


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# 9 個連續維度 + 1 個 multi_discrete loop_capability（list[float]）
_COMPLETE_CONTINUOUS_DIMS = {
    "valence": 0.7,
    "arousal": 0.6,
    "emotional_warmth": 0.5,
    "tension_direction": 0.3,
    "temporal_position": 0.25,
    "event_significance": 0.6,
    "tonal_noise_ratio": 0.8,
    "spectral_density": 0.5,
    "world_immersion": 0.7,
}
_DEFAULT_LOOP_CAPABILITY: list[float] = [1.0]


def _make_audio(session: Session, filename: str = "Foo_Base Game.wav") -> AudioFile:
    audio = AudioFile(
        filename=filename,
        game_name=filename.split("_")[0],
        game_stage=filename.split("_")[1].removesuffix(".wav"),
    )
    session.add(audio)
    session.commit()
    session.refresh(audio)
    return audio


def _make_annotation(
    session: Session,
    audio_id: str,
    annotator_id: str,
    *,
    is_complete: bool = True,
    dims: dict | None = None,
    loop_capability: list[float] | None = None,
    source_type: list[str] | None = None,
    function_roles: list | None = None,
    style_tag: list | None = None,
    genre_tag: list[str] | None = None,
    worldview_tag: str | None = "asian_mythology",
    notes: str | None = None,
) -> Annotation:
    """fixture：注意 dims 只接連續維度；multi_discrete 走專屬 loop_capability 參數。"""
    overrides = dict(dims or {})
    # 向後相容：若呼叫端在 dims 裡塞 loop_capability，將其抽出
    if "loop_capability" in overrides:
        loop_capability = overrides.pop("loop_capability")
        if not isinstance(loop_capability, list):
            loop_capability = [loop_capability]
    continuous = {**_COMPLETE_CONTINUOUS_DIMS, **overrides}
    ann = Annotation(
        audio_file_id=audio_id,
        annotator_id=annotator_id,
        loop_capability=json.dumps(
            loop_capability if loop_capability is not None else _DEFAULT_LOOP_CAPABILITY
        ),
        source_type=json.dumps(source_type if source_type is not None else ["ambience"]),
        function_roles=json.dumps(function_roles or ["atmosphere"]),
        style_tag=json.dumps(style_tag or ["chinese_traditional"]),
        genre_tag=json.dumps(genre_tag if genre_tag is not None else ["博弈"]),
        worldview_tag=worldview_tag,
        notes=notes,
        is_complete=is_complete,
        **continuous,
    )
    session.add(ann)
    session.commit()
    session.refresh(ann)
    return ann


# ---------------------------------------------------------------------------
# 1. single annotator
# ---------------------------------------------------------------------------

def test_single_annotator_uses_single_annotator_method(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", dims={"valence": 0.77})
        data = build_dataset(s)

    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["consensus_method"] == "single_annotator"
    # single annotator 情境：dimensions 就是該人的值（單筆 mean == 自己，round 到 3）
    assert item["consensus"]["dimensions"]["valence"] == 0.77
    assert item["consensus"]["source_type"] == ["ambience"]
    assert data["total_annotated"] == 1
    assert data["total_annotations"] == 1
    assert data["annotators"] == ["amber"]


# ---------------------------------------------------------------------------
# 2. 兩人連續 mean
# ---------------------------------------------------------------------------

def test_two_annotators_mean_continuous_dimensions(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", dims={"valence": 0.6, "arousal": 0.4})
        _make_annotation(s, audio.id, "bob",   dims={"valence": 0.8, "arousal": 0.6})
        data = build_dataset(s)

    item = data["items"][0]
    assert item["consensus_method"] == "mixed"
    assert item["consensus"]["dimensions"]["valence"] == 0.7
    assert item["consensus"]["dimensions"]["arousal"] == 0.5
    assert sorted(ann["annotator_id"] for ann in item["individual_annotations"]) == ["amber", "bob"]


# ---------------------------------------------------------------------------
# 3. function_roles union
# ---------------------------------------------------------------------------

def test_function_roles_union(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", function_roles=["atmosphere", "musical_sfx"])
        _make_annotation(s, audio.id, "bob",   function_roles=["atmosphere", "ui"])
        data = build_dataset(s)

    roles = data["items"][0]["consensus"]["function_roles"]
    assert set(roles) == {"atmosphere", "musical_sfx", "ui"}
    assert len(roles) == 3  # dedupe 生效


# ---------------------------------------------------------------------------
# 4. source_type union（兩人選不同 → consensus 取兩個 dedupe）
# ---------------------------------------------------------------------------

def test_source_type_union_across_annotators(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", source_type=["synthetic_designed"])
        _make_annotation(s, audio.id, "bob",   source_type=["ambience", "synthetic_designed"])
        data = build_dataset(s)

    item = data["items"][0]
    consensus_src = item["consensus"]["source_type"]
    assert isinstance(consensus_src, list)
    assert set(consensus_src) == {"synthetic_designed", "ambience"}
    # 不再產生 source_type_conflict warning
    assert "warnings" not in item or "source_type_conflict" not in item.get("warnings", [])


# ---------------------------------------------------------------------------
# 5. is_complete=False 被排除
# ---------------------------------------------------------------------------

def test_incomplete_annotation_excluded(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio_a = _make_audio(s, "A_Base Game.wav")
        audio_b = _make_audio(s, "B_Base Game.wav")
        # audio_a：一人完成、一人 draft → 只算完成那人
        _make_annotation(s, audio_a.id, "amber", is_complete=True, dims={"valence": 0.5})
        _make_annotation(s, audio_a.id, "bob",   is_complete=False, dims={"valence": 0.9})
        # audio_b：全員 draft → 整檔被排除
        _make_annotation(s, audio_b.id, "amber", is_complete=False)
        data = build_dataset(s)

    # audio_b 不在 items 裡
    filenames = [it["audio_file"] for it in data["items"]]
    assert "A_Base Game.wav" in filenames
    assert "B_Base Game.wav" not in filenames

    # audio_a 的 consensus 只有 amber 的值，沒被 bob 的 0.9 影響
    item_a = next(it for it in data["items"] if it["audio_file"] == "A_Base Game.wav")
    assert item_a["consensus"]["dimensions"]["valence"] == 0.5
    assert item_a["consensus_method"] == "single_annotator"
    assert len(item_a["individual_annotations"]) == 1

    # 分母計數：2 個音檔、1 個有完整標註、只有 amber 的 1 筆完成 annotation
    assert data["total_audio_files"] == 2
    assert data["total_annotated"] == 1
    assert data["total_annotations"] == 1


# ---------------------------------------------------------------------------
# 6. loop_capability multi_discrete：三方各選不同 → consensus 取 union
# ---------------------------------------------------------------------------

def test_loop_capability_union_across_annotators(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", loop_capability=[0.0])
        _make_annotation(s, audio.id, "bob",   loop_capability=[0.5])
        _make_annotation(s, audio.id, "carol", loop_capability=[1.0])
        data = build_dataset(s)

    consensus_loop = data["items"][0]["consensus"]["dimensions"]["loop_capability"]
    assert isinstance(consensus_loop, list)
    assert sorted(consensus_loop) == [0.0, 0.5, 1.0]


def test_loop_capability_single_annotator_passes_through_list(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", loop_capability=[0.5, 1.0])
        data = build_dataset(s)

    assert data["items"][0]["consensus"]["dimensions"]["loop_capability"] == [0.5, 1.0]


def test_genre_tag_union_across_annotators(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", genre_tag=["博弈", "東方"])
        _make_annotation(s, audio.id, "bob",   genre_tag=["博弈", "電子"])
        data = build_dataset(s)

    consensus_genre = data["items"][0]["consensus"]["genre_tag"]
    assert set(consensus_genre) == {"博弈", "東方", "電子"}
    assert len(consensus_genre) == 3  # dedupe


# ---------------------------------------------------------------------------
# 7. 空 DB → items=[]
# ---------------------------------------------------------------------------

def test_empty_database_does_not_crash(in_memory_engine):
    with Session(in_memory_engine) as s:
        data = build_dataset(s)

    assert data["items"] == []
    assert data["total_audio_files"] == 0
    assert data["total_annotated"] == 0
    assert data["total_annotations"] == 0
    assert data["annotators"] == []
    # schema_version / dimension_schema 仍在，買方 parser 不會炸
    assert data["schema_version"] == "0.2.0"
    assert "valence" in data["dimension_schema"]


# ---------------------------------------------------------------------------
# 8. individual.json 未知 annotator → 404（route 層）
# ---------------------------------------------------------------------------

def test_individual_endpoint_unknown_annotator_returns_404(client, in_memory_engine):
    # DB 空，任何 annotator 都應該 404（不要回 200 帶空 items）
    r = client.get("/api/export/individual.json", params={"annotator": "ghost"})
    assert r.status_code == 404


def test_individual_endpoint_existing_annotator_with_no_completed_returns_404(
    client, in_memory_engine,
):
    """存在該 annotator，但他所有 annotation 都是 draft → 也該 404。"""
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", is_complete=False)

    r = client.get("/api/export/individual.json", params={"annotator": "amber"})
    assert r.status_code == 404


def test_individual_endpoint_existing_annotator_with_completed_returns_200(
    client, in_memory_engine,
):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", is_complete=True)

    r = client.get("/api/export/individual.json", params={"annotator": "amber"})
    assert r.status_code == 200
    data = r.json()
    assert data["annotators"] == ["amber"]
    assert len(data["items"]) == 1


# ---------------------------------------------------------------------------
# 附加：dataset endpoint 的 smoke test（驗 JSON 路徑能打通 serialization）
# ---------------------------------------------------------------------------

def test_dataset_endpoint_returns_valid_envelope(client, in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber")

    r = client.get("/api/export/dataset.json")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "0.2.0"
    assert data["total_annotated"] == 1
    # function_roles 在 HTTP body 裡應該是真的 array，不是 escape string
    roles = data["items"][0]["consensus"]["function_roles"]
    assert isinstance(roles, list)
    assert roles == ["atmosphere"]


def test_calibration_endpoint_only_contains_amber(client, in_memory_engine):
    with Session(in_memory_engine) as s:
        audio_a = _make_audio(s, "A_Base Game.wav")
        audio_b = _make_audio(s, "B_Base Game.wav")
        _make_annotation(s, audio_a.id, "amber")
        _make_annotation(s, audio_b.id, "bob")

    r = client.get("/api/export/calibration_set.json")
    assert r.status_code == 200
    data = r.json()
    assert data["annotators"] == ["amber"]
    # bob 的那檔整個不進 items
    filenames = [it["audio_file"] for it in data["items"]]
    assert filenames == ["A_Base Game.wav"]


# ---------------------------------------------------------------------------
# Phase 10: min_status filter
# ---------------------------------------------------------------------------

def test_export_min_status_gold_excludes_unlocked(client, in_memory_engine):
    """min_status=gold 只回 is_gold_locked=True 的音檔。"""
    with Session(in_memory_engine) as s:
        audio_a = _make_audio(s, "A_Base Game.wav")
        audio_b = _make_audio(s, "B_Base Game.wav")
        # 兩個 audio 都有 amber + bob 兩人共標(緊 spread)
        _make_annotation(s, audio_a.id, "amber")
        _make_annotation(s, audio_a.id, "bob")
        _make_annotation(s, audio_b.id, "amber")
        _make_annotation(s, audio_b.id, "bob")
        # 只 lock A
        audio_a.is_gold_locked = True
        s.add(audio_a); s.commit()

    r = client.get("/api/export/dataset.json?min_status=gold")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["min_status"] == "gold"
    filenames = [it["audio_file"] for it in data["items"]]
    assert filenames == ["A_Base Game.wav"]


def test_export_min_status_cross_includes_lockable_and_gold(client, in_memory_engine):
    """min_status=cross_annotated 該含 lockable + gold,但不含 untouched / draft。"""
    with Session(in_memory_engine) as s:
        audio_draft = _make_audio(s, "D_Base Game.wav")
        audio_cross = _make_audio(s, "C_Base Game.wav")
        # draft: 1 人
        _make_annotation(s, audio_draft.id, "amber")
        # cross: 2 人緊 spread(會被算成 lockable,但 >= cross_annotated)
        _make_annotation(s, audio_cross.id, "amber")
        _make_annotation(s, audio_cross.id, "bob")

    r = client.get("/api/export/dataset.json?min_status=cross_annotated")
    assert r.status_code == 200
    filenames = [it["audio_file"] for it in r.json()["items"]]
    assert filenames == ["C_Base Game.wav"]


def test_export_invalid_min_status_returns_400(client, in_memory_engine):
    r = client.get("/api/export/dataset.json?min_status=invalid_state")
    assert r.status_code == 400


def test_export_default_min_status_is_untouched_backward_compat(client, in_memory_engine):
    """無 min_status param → 預設 untouched(全部,行為跟 Phase 4 一致)。"""
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber")
    r = client.get("/api/export/dataset.json")
    assert r.status_code == 200
    data = r.json()
    assert data["min_status"] == "untouched"
    assert len(data["items"]) == 1


# ---------------------------------------------------------------------------
# Phase 7：acoustic 兩維由 librosa 寫 AudioFile，consensus 直取不做 human aggregation
# ---------------------------------------------------------------------------

def test_acoustic_consensus_uses_librosa_not_human_aggregation(in_memory_engine):
    """consensus.dimensions.tonal_noise_ratio / spectral_density 必須來自 audio.*_auto。

    刻意讓 annotation 裡的 human acoustic 值跟 audio.*_auto 顯著衝突，驗 librosa 勝出。
    這 2 維是音檔的物理屬性，不能被人類主觀標註覆蓋。
    """
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        # 模擬 librosa 已 cache 的結果（實際路徑：上傳時 ensure_cached 寫入）
        audio.tonal_noise_ratio_auto = 0.85
        audio.spectral_density_auto = 0.42
        s.add(audio)
        s.commit()
        s.refresh(audio)
        # 兩個 human annotator 的 acoustic 標註值跟 librosa 完全不同
        _make_annotation(
            s, audio.id, "amber",
            dims={"tonal_noise_ratio": 0.1, "spectral_density": 0.9},
        )
        _make_annotation(
            s, audio.id, "bob",
            dims={"tonal_noise_ratio": 0.2, "spectral_density": 0.8},
        )
        data = build_dataset(s)

    consensus_dims = data["items"][0]["consensus"]["dimensions"]
    assert consensus_dims["tonal_noise_ratio"] == 0.85, "必須取 audio.*_auto，不取 human mean"
    assert consensus_dims["spectral_density"] == 0.42, "必須取 audio.*_auto，不取 human mean"
    # 但 individual_annotations 保留 human 值作為「人類聽覺偏誤」研究素材
    inds = data["items"][0]["individual_annotations"]
    assert {ind["dimensions"]["tonal_noise_ratio"] for ind in inds} == {0.1, 0.2}


def test_consensus_dimension_sources_metadata_present(in_memory_engine):
    """每維度標明 source — 買方 parser / SITI 審查員可看 librosa_v1 vs human_consensus。"""
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber")
        data = build_dataset(s)

    sources = data["items"][0]["consensus"]["dimension_sources"]
    assert sources["tonal_noise_ratio"] == "librosa_v1"
    assert sources["spectral_density"] == "librosa_v1"
    assert sources["valence"] == "human_consensus"
    assert sources["arousal"] == "human_consensus"
    assert sources["loop_capability"] == "human_consensus"


def test_acoustic_null_when_audio_auto_not_cached(in_memory_engine):
    """audio.*_auto 為 None 時（尚未 backfill），consensus 也是 None — 不 fallback 到 human。

    這是刻意設計：force backfill 跑完才能匯出。半途資料更糟。
    """
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        # 不設 audio.*_auto，模擬尚未跑 backfill
        _make_annotation(
            s, audio.id, "amber",
            dims={"tonal_noise_ratio": 0.5, "spectral_density": 0.5},
        )
        data = build_dataset(s)

    consensus_dims = data["items"][0]["consensus"]["dimensions"]
    assert consensus_dims["tonal_noise_ratio"] is None
    assert consensus_dims["spectral_density"] is None

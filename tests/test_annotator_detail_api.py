"""GET /api/stats/annotator/{id}/detail 整合測試 + 頁面路由。

沿用 test_stats.py 的 fixture/override 慣例（conftest 的 in_memory_engine / client，
dependency_overrides[require_auth] 模擬登入者）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from src.models import Annotation, AudioFile


def _add_audio(engine, filename: str) -> str:
    with Session(engine) as s:
        a = AudioFile(filename=filename, game_name=filename.split("_")[0],
                       game_stage="Base Game", duration_sec=9.2)
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def _add_annotation(engine, audio_id: str, annotator_id: str, *,
                     is_complete: bool = True, valence: float | None = 0.5,
                     created_at: datetime, updated_at: datetime | None = None,
                     source_type: str = '["bgm"]', **dim_overrides) -> None:
    """dim_overrides 可帶任意維度值(如 tonal_noise_ratio=0.8),給警示偏差測試用。"""
    if updated_at is None:
        updated_at = created_at + timedelta(minutes=5)
    with Session(engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id=annotator_id,
            is_complete=is_complete, valence=valence,
            source_type=source_type, function_roles='["atmosphere"]',
            style_tag="[]", created_at=created_at, updated_at=updated_at,
            **dim_overrides,
        ))
        s.commit()


def _override_user(annotator_id: str, *, is_admin: bool):
    from src import main as main_module
    from src.middleware import require_auth
    main_module.app.dependency_overrides[require_auth] = lambda: {
        "annotator_id": annotator_id, "email": None,
        "is_admin": is_admin, "name": None,
    }


NOW = datetime(2026, 5, 12, 3, 0, tzinfo=UTC)


def test_admin_sees_other_annotator(client, in_memory_engine):
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_audio(in_memory_engine, "G2_x.wav")
    _add_annotation(in_memory_engine, a1, "yyslin1024", created_at=NOW)
    _override_user("amber", is_admin=True)

    r = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["annotator_id"] == "yyslin1024"
    assert body["progress"]["completed_count"] == 1
    assert body["progress"]["total_audio_files"] == 2
    assert len(body["files"]) == 1
    assert body["files"][0]["filename"] == "G1_x.wav"
    assert body["files"][0]["source_type"] == ["bgm"]
    assert body["files"][0]["game_name"] == "G1"


def test_self_sees_self(client, in_memory_engine):
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_annotation(in_memory_engine, a1, "yyslin1024", created_at=NOW)
    _override_user("yyslin1024", is_admin=False)

    r = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC")
    assert r.status_code == 200, r.text
    assert r.json()["annotator_id"] == "yyslin1024"


def test_non_admin_non_self_forbidden(client, in_memory_engine):
    _add_audio(in_memory_engine, "G1_x.wav")
    _override_user("yyslin1024", is_admin=False)

    r = client.get("/api/stats/annotator/amber/detail?tz=UTC")
    assert r.status_code == 403


def test_amber_has_no_calibration(client, in_memory_engine):
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_annotation(in_memory_engine, a1, "amber", created_at=NOW)
    _override_user("amber", is_admin=True)

    r = client.get("/api/stats/annotator/amber/detail?tz=UTC")
    assert r.status_code == 200, r.text
    assert r.json()["calibration"] is None


def test_no_overlap_has_no_calibration(client, in_memory_engine):
    # yyslin1024 標 a1，amber 標 a2 → 無重疊 → calibration None
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    a2 = _add_audio(in_memory_engine, "G2_x.wav")
    _add_annotation(in_memory_engine, a1, "yyslin1024", created_at=NOW)
    _add_annotation(in_memory_engine, a2, "amber", created_at=NOW)
    _override_user("amber", is_admin=True)

    r = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC")
    assert r.status_code == 200, r.text
    assert r.json()["calibration"] is None


def test_overlap_produces_calibration_summary(client, in_memory_engine):
    # 同一 audio amber valence=0.5 / yyslin1024 valence=0.8 → mae 0.3
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_annotation(in_memory_engine, a1, "amber", valence=0.5, created_at=NOW)
    _add_annotation(in_memory_engine, a1, "yyslin1024", valence=0.8, created_at=NOW)
    _override_user("amber", is_admin=True)

    r = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC")
    assert r.status_code == 200, r.text
    cal = r.json()["calibration"]
    assert cal is not None
    assert cal["total_overlap"] == 1
    assert abs(cal["overall_mae"] - 0.3) < 1e-6
    assert cal["worst_dim"] == "valence"
    assert cal["report_url"] == "/calibration/report?annotator=yyslin1024"


def test_files_only_complete_and_sorted_desc(client, in_memory_engine):
    a1 = _add_audio(in_memory_engine, "G1_a.wav")
    a2 = _add_audio(in_memory_engine, "G2_b.wav")
    a3 = _add_audio(in_memory_engine, "G3_c.wav")
    _add_annotation(in_memory_engine, a1, "yyslin1024",
                    created_at=NOW, updated_at=NOW + timedelta(hours=1))
    _add_annotation(in_memory_engine, a2, "yyslin1024",
                    created_at=NOW, updated_at=NOW + timedelta(hours=3))
    # a3 未完成 → 不應出現
    _add_annotation(in_memory_engine, a3, "yyslin1024", is_complete=False,
                    created_at=NOW, updated_at=NOW + timedelta(hours=9))
    _override_user("amber", is_admin=True)

    files = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC").json()["files"]
    assert [f["filename"] for f in files] == ["G2_b.wav", "G1_a.wav"]


def test_detail_page_route_serves_html(client):
    r = client.get("/annotator/yyslin1024")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "annotator-detail.js" in r.text


# ─── Feature-005: 每筆 vs Amber 偏差警示 ────────────────────────

def test_warning_dims_flag_subjective_over_threshold(client, in_memory_engine):
    """主觀維度差距 >= 0.25 → 警示;< 0.25 → 不警示。max_deviation 一律回實際最大差。"""
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    a2 = _add_audio(in_memory_engine, "G2_x.wav")
    # amber valence 0.2;yy a1 valence 0.5(差 0.3 ≥ 0.25 警示)、a2 valence 0.3(差 0.1 不警示)
    _add_annotation(in_memory_engine, a1, "amber", valence=0.2, created_at=NOW)
    _add_annotation(in_memory_engine, a2, "amber", valence=0.2, created_at=NOW)
    _add_annotation(in_memory_engine, a1, "yyslin1024", valence=0.5, created_at=NOW)
    _add_annotation(in_memory_engine, a2, "yyslin1024", valence=0.3, created_at=NOW)
    _override_user("amber", is_admin=True)

    files = {f["filename"]: f
             for f in client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC").json()["files"]}
    assert files["G1_x.wav"]["warning_count"] == 1
    assert files["G1_x.wav"]["warning_dims"][0]["dim"] == "valence"
    assert abs(files["G1_x.wav"]["warning_dims"][0]["diff"] - 0.3) < 1e-6
    assert abs(files["G1_x.wav"]["max_deviation"] - 0.3) < 1e-6
    # a2 沒警示,但 max_deviation 仍記 0.1
    assert files["G2_x.wav"]["warning_count"] == 0
    assert abs(files["G2_x.wav"]["max_deviation"] - 0.1) < 1e-6


def test_warning_dims_objective_threshold_015(client, in_memory_engine):
    """客觀維度(tonal_noise_ratio)門檻 0.15:差 0.2 → 警示。"""
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_annotation(in_memory_engine, a1, "amber",
                    valence=0.5, tonal_noise_ratio=0.3, created_at=NOW)
    _add_annotation(in_memory_engine, a1, "yyslin1024",
                    valence=0.5, tonal_noise_ratio=0.5, created_at=NOW)  # 差 0.2 ≥ 0.15
    _override_user("amber", is_admin=True)

    f = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC").json()["files"][0]
    dims = {w["dim"] for w in f["warning_dims"]}
    assert "tonal_noise_ratio" in dims
    # valence 差 0 不該警示
    assert "valence" not in dims


def test_warning_none_when_amber_did_not_annotate(client, in_memory_engine):
    """Amber 沒標過該 audio → 無從比對 → warning_count=0、max_deviation=None。"""
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_annotation(in_memory_engine, a1, "yyslin1024", valence=0.9, created_at=NOW)
    _override_user("amber", is_admin=True)

    f = client.get("/api/stats/annotator/yyslin1024/detail?tz=UTC").json()["files"][0]
    assert f["warning_count"] == 0
    assert f["max_deviation"] is None


def test_warning_amber_self_detail_no_warnings(client, in_memory_engine):
    """amber 自己的明細不做 self-comparison → 全部 warning_count=0。"""
    a1 = _add_audio(in_memory_engine, "G1_x.wav")
    _add_annotation(in_memory_engine, a1, "amber", valence=0.9, created_at=NOW)
    _override_user("amber", is_admin=True)

    f = client.get("/api/stats/annotator/amber/detail?tz=UTC").json()["files"][0]
    assert f["warning_count"] == 0
    assert f["max_deviation"] is None

"""Phase 1 smoke tests：路由能起、回傳預期 shape。

不測 lifespan（lifespan 會掃 real data/audio/ 與寫 real DB，會污染測試環境）。
"""
from sqlmodel import Session

from src.audio_scanner import scan_audio_directory


def test_index_returns_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "珀瀾聲音標註工具" in r.text


def test_annotate_returns_html_with_audio_id(client):
    r = client.get("/annotate/test-id?annotator=amber")
    assert r.status_code == 200
    assert "waveform" in r.text


def test_static_list_js_served(client):
    r = client.get("/static/list.js")
    assert r.status_code == 200
    assert "/api/audio" in r.text


def test_dimensions_endpoint_returns_ten(client):
    r = client.get("/api/dimensions")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 10
    assert "valence" in data
    assert "world_immersion" in data


def test_dimensions_spec_includes_required_fields(client):
    r = client.get("/api/dimensions")
    valence = r.json()["valence"]
    for field in ("label_zh", "category", "type", "definition", "amber_confirmed"):
        assert field in valence


def test_audio_endpoint_empty_initially(client):
    r = client.get("/api/audio")
    assert r.status_code == 200
    assert r.json() == []


def test_audio_endpoint_returns_scanned_files(client, in_memory_engine, tmp_path):
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "Volcano Goddess_Base Game.wav").touch()
    (audio_dir / "Volcano Goddess_Free Game.wav").touch()

    with Session(in_memory_engine) as s:
        scan_audio_directory(s, audio_dir=audio_dir)

    r = client.get("/api/audio")
    items = r.json()
    assert len(items) == 2
    assert items[0]["game_name"] == "Volcano Goddess"
    assert {item["game_stage"] for item in items} == {"Base Game", "Free Game"}

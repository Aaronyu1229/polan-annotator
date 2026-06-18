"""發佈服務：複製音檔進獨立倉 + 建 AlignmentAudio + ClientLink。"""
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.alignment_db import AlignmentAudio, AlignmentBase, ClientLink
from src.alignment_publish import publish_audio_link
from src.client_auth import hash_token


def _align_session():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    AlignmentBase.metadata.create_all(eng)
    return Session(eng)


def test_publish_copies_file_and_creates_rows(tmp_path):
    src_dir = tmp_path / "audio"
    dst_dir = tmp_path / "alignment_audio"
    src_dir.mkdir()
    (src_dir / "ref.wav").write_bytes(b"RIFF0000WAVE")

    db = _align_session()
    res = publish_audio_link(
        src_filename="ref.wav", label="客戶A", role="client",
        annotator_id="cli1", session_id="s1", expires_at=None,
        align_db=db, src_audio_dir=src_dir, dst_audio_dir=dst_dir,
        orig_audio_id="orig1",
    )

    # 檔案被複製到獨立倉，原檔還在
    assert (dst_dir / "ref.wav").read_bytes() == b"RIFF0000WAVE"
    assert (src_dir / "ref.wav").exists()

    aa = db.get(AlignmentAudio, res.alignment_audio_id)
    assert aa.filename == "ref.wav"
    assert aa.orig_audio_id == "orig1"

    link = db.scalars(
        select(ClientLink).where(ClientLink.id == res.link_id)
    ).first()
    assert link.role == "client"
    assert link.session_id == res.session_id
    assert link.alignment_audio_id == res.alignment_audio_id
    # DB 只存 hash，回傳明文 token 的 hash 要對得上
    assert link.token_hash == hash_token(res.token)


def test_publish_missing_source_raises(tmp_path):
    db = _align_session()
    try:
        publish_audio_link(
            src_filename="nope.wav", label="A", role="client",
            annotator_id="cli1", session_id="s1", expires_at=None,
            align_db=db, src_audio_dir=tmp_path, dst_audio_dir=tmp_path / "out",
        )
        assert False, "should have raised"
    except FileNotFoundError:
        pass

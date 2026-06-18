"""AlignmentAudio + ClientLink model 的 DB 層測試（in-memory alignment 庫）。"""
from datetime import datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.alignment_db import AlignmentAudio, AlignmentBase, ClientLink


def _mem_session() -> Session:
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AlignmentBase.metadata.create_all(eng)
    return Session(eng)


def test_alignment_audio_insert_and_read():
    s = _mem_session()
    row = AlignmentAudio(id="aa1", filename="ref.wav", orig_audio_id="src1")
    s.add(row)
    s.commit()
    got = s.get(AlignmentAudio, "aa1")
    assert got.filename == "ref.wav"
    assert got.orig_audio_id == "src1"
    assert isinstance(got.created_at, datetime)


def test_client_link_insert_and_query_by_hash():
    s = _mem_session()
    s.add(ClientLink(
        id="cl1", token_hash="deadbeef", role="client", label="客戶A",
        annotator_id="cli1", session_id="s1", alignment_audio_id="aa1",
    ))
    s.commit()
    found = s.scalars(
        select(ClientLink).where(ClientLink.token_hash == "deadbeef")
    ).first()
    assert found.role == "client"
    assert found.revoked is False
    assert found.expires_at is None

"""BGM 對齊比對引擎 —— 一個引擎、四種比對（不做四個畫面）。

四種需求只是 A、B 選不同的兩筆 reading（或比對 3 選 N 筆）：
  1 音效師 vs 客戶   只變 who            → 差距＝認知落差
  2 第一版 vs 第二版 只變 version        → 差距＝改版收斂幅度
  3 同關卡不同 ref   只變 audio          → **變異**（穩定=保留項 / 飄動=可自由發揮）
  4 聽到 vs 預期     只變 reading_type   → 差距＝要往哪調多少

兩個判讀規則：
- 一次只變一個軸（differing_axes / PairResult.valid 守門）。
- 比對 3 看「變異」（compute_variance），其餘看「差距」（compare_pair）。同資料、不同讀法。

純函數、不依賴 DB —— 方便單獨測試。spec:
docs/superpowers/specs/2026-06-18-bgm-alignment-mode-design.md
"""
from dataclasses import dataclass, field
from typing import Sequence

# BGM 模式的四條感受滑桿（隱藏 arousal / event_significance）
BGM_DIMENSIONS: tuple[str, ...] = (
    "valence",
    "tension_direction",
    "emotional_warmth",
    "world_immersion",
)


@dataclass(frozen=True)
class Reading:
    """單一維度的一筆值（long format，對應 AlignmentReading 一 row）。"""
    session_id: str
    annotator_id: str
    annotator_role: str   # engineer | client
    audio_id: str
    audio_role: str       # ref | deliverable
    version: int
    dimension: str
    value: float
    reading_type: str     # perceived | target
    level_id: str = ""


@dataclass(frozen=True)
class SetIdentity:
    """一組維度值的身分（除 dimension/value 外的定位標籤）。"""
    session_id: str
    annotator_id: str
    annotator_role: str
    audio_id: str
    audio_role: str
    version: int
    reading_type: str
    level_id: str = ""


@dataclass(frozen=True)
class ReadingSet:
    """某標註者對某音源在某時點的整組維度值。"""
    identity: SetIdentity
    values: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class PairResult:
    """一對比對的結果。valid=False 代表變了不只一個軸（差距無意義，UI 應警示）。"""
    diffs: dict[str, float]
    differing_axes: list[str]
    valid: bool


def _identity_of(r: Reading) -> SetIdentity:
    return SetIdentity(
        session_id=r.session_id,
        level_id=r.level_id,
        annotator_id=r.annotator_id,
        annotator_role=r.annotator_role,
        audio_id=r.audio_id,
        audio_role=r.audio_role,
        version=r.version,
        reading_type=r.reading_type,
    )


def group_into_sets(readings: Sequence[Reading]) -> list[ReadingSet]:
    """把 long-format readings 依身分聚成 ReadingSet（每身分一組維度→值）。

    回傳順序＝各身分首次出現的順序（穩定，方便測試與 UI）。
    """
    order: list[SetIdentity] = []
    grouped: dict[SetIdentity, dict[str, float]] = {}
    for r in readings:
        ident = _identity_of(r)
        if ident not in grouped:
            grouped[ident] = {}
            order.append(ident)
        grouped[ident][r.dimension] = r.value
    return [ReadingSet(ident, grouped[ident]) for ident in order]


def differing_axes(a: SetIdentity, b: SetIdentity) -> list[str]:
    """回傳 a、b 之間有差異的軸。合法的一對比對應只差一個軸。

    "who" 把 (annotator_role, annotator_id) 視為一軸：比對 1 音效師↔客戶 同時換
    role 與 id，但概念上只變「是誰標的」一個軸。session / audio_role 差異也會被列出
    （代表跨情境比對，應視為 invalid）。
    """
    axes: list[str] = []
    if (a.annotator_role, a.annotator_id) != (b.annotator_role, b.annotator_id):
        axes.append("who")
    if a.session_id != b.session_id:
        axes.append("session")
    if a.level_id != b.level_id:
        axes.append("level")
    if a.audio_role != b.audio_role:
        axes.append("audio_role")
    if a.version != b.version:
        axes.append("version")
    if a.audio_id != b.audio_id:
        axes.append("audio")
    if a.reading_type != b.reading_type:
        axes.append("reading_type")
    return axes


def compare_pair(
    a: ReadingSet,
    b: ReadingSet,
    dimensions: Sequence[str] = BGM_DIMENSIONS,
) -> dict[str, float]:
    """每維絕對差距；只算兩邊都有值的維度。"""
    return {
        dim: abs(a.values[dim] - b.values[dim])
        for dim in dimensions
        if dim in a.values and dim in b.values
    }


def pair_comparison(
    a: ReadingSet,
    b: ReadingSet,
    dimensions: Sequence[str] = BGM_DIMENSIONS,
) -> PairResult:
    """比對 1/2/4 的入口：算差距 + 檢查是否只變一個軸。

    即使 invalid（變了多軸）仍回傳 diffs，讓 UI 決定如何警示（spec §6 規則 1）。
    """
    axes = differing_axes(a.identity, b.identity)
    return PairResult(
        diffs=compare_pair(a, b, dimensions),
        differing_axes=axes,
        valid=len(axes) == 1,
    )


def compute_variance(
    sets: Sequence[ReadingSet],
    dimensions: Sequence[str] = BGM_DIMENSIONS,
) -> dict[str, float]:
    """比對 3：跨多筆 reading（多首 ref）每維的 spread = max - min。

    spread 小＝該維穩定＝客戶在意、鎖定（保留項）；spread 大＝可自由發揮。
    沿用本 repo 既有的「spread = max-min」慣例。只算至少一組有值的維度。
    """
    spread: dict[str, float] = {}
    for dim in dimensions:
        vals = [s.values[dim] for s in sets if dim in s.values]
        if vals:
            spread[dim] = max(vals) - min(vals)
    return spread

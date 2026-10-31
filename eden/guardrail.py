"""ガードレール: Rekognition による顔/人物/不適切画像の3段チェック。

登録フローの最初に呼び出し、不適切な画像の登録を防止する。
チェック順序:
  1. detect_faces → 顔が検出されたら拒否 (ポスター・フィギュアの顔も含む)
  2. detect_labels → Person/Human の Instances があれば拒否
  3. detect_moderation_labels → NSFW系コンテンツを拒否
いずれか1つでも引っかかった時点で即拒否を返す(短絡評価)。
"""

from dataclasses import dataclass, field

from eden import config
from eden.clients import get_rekognition_client


@dataclass
class GuardrailResult:
    """ガードレール判定結果。"""

    passed: bool
    reason: str | None = None  # 拒否理由 (passedがTrueならNone)
    details: list[str] = field(default_factory=list)  # 検出されたラベル/理由の一覧


def check_guardrail(
    image_bytes: bytes,
    *,
    client=None,
) -> GuardrailResult:
    """画像に対して3段階のガードレールチェックを実行する。

    Args:
        image_bytes: 画像のバイト列 (JPEG/PNG)
        client: rekognition クライアント (Noneならデフォルト生成)

    Returns:
        GuardrailResult
    """
    client = client or get_rekognition_client()
    image_param = {"Bytes": image_bytes}

    # --- 1. detect_faces: 顔検出 ---
    result = _check_faces(client, image_param)
    if result is not None:
        return result

    # --- 2. detect_labels: 人物検出 ---
    result = _check_person_labels(client, image_param)
    if result is not None:
        return result

    # --- 3. detect_moderation_labels: 不適切コンテンツ ---
    result = _check_moderation(client, image_param)
    if result is not None:
        return result

    return GuardrailResult(passed=True)


def _check_faces(client, image_param: dict) -> GuardrailResult | None:
    """顔検出チェック。顔が1つでも検出されたら拒否。"""
    resp = client.detect_faces(
        Image=image_param,
        Attributes=["DEFAULT"],
    )
    faces = resp.get("FaceDetails", [])
    if faces:
        details = [
            f"Face detected (confidence: {face['Confidence']:.1f}%)"
            for face in faces
        ]
        return GuardrailResult(
            passed=False,
            reason="face_detected",
            details=details,
        )
    return None


def _check_person_labels(client, image_param: dict) -> GuardrailResult | None:
    """人物ラベル検出チェック。Person/HumanのInstancesがあれば拒否。"""
    resp = client.detect_labels(
        Image=image_param,
        MaxLabels=50,
    )
    labels = resp.get("Labels", [])

    person_labels = []
    for label in labels:
        name = label.get("Name", "")
        if name.lower() in ("person", "human"):
            instances = label.get("Instances", [])
            if instances:
                person_labels.append(
                    f"{name}: {len(instances)} instance(s) "
                    f"(confidence: {label['Confidence']:.1f}%)"
                )

    if person_labels:
        return GuardrailResult(
            passed=False,
            reason="person_detected",
            details=person_labels,
        )
    return None


def _check_moderation(client, image_param: dict) -> GuardrailResult | None:
    """不適切コンテンツ検出チェック。信頼度閾値以上のラベルがあれば拒否。"""
    min_confidence = config.GUARDRAIL_MODERATION_MIN_CONFIDENCE

    resp = client.detect_moderation_labels(
        Image=image_param,
        MinConfidence=min_confidence,
    )
    moderation_labels = resp.get("ModerationLabels", [])

    if moderation_labels:
        details = [
            f"{ml['Name']} (parent: {ml.get('ParentName', 'N/A')}, "
            f"confidence: {ml['Confidence']:.1f}%)"
            for ml in moderation_labels
        ]
        return GuardrailResult(
            passed=False,
            reason="inappropriate_content",
            details=details,
        )
    return None

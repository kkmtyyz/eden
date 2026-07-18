import boto3
import json
import sys
import re
from pathlib import Path
from PIL import Image, ImageDraw

# REGION = "us-east-1"
REGION = "ap-northeast-1"
MODEL_ID = "amazon.nova-lite-v1:0"
MAX_SIZE = 1280  # 送信前に縮小(コスト・レイテンシ対策)

session = boto3.Session(profile_name="kkmtyyz_mfa")
client = session.client("bedrock-runtime", region_name=REGION)

PROMPT = """Detect all distinct physical objects in this image.
For each object, output its bounding box on a scale of [0, 1000).
Output ONLY a LIST, no other text:
[ [x1, y1, x2, y2], ...]"""
# PROMPT = """Detect all distinct physical objects in this image.
# For each object, output its bounding box on a scale of [0, 1000).
# Output ONLY a JSON array, no other text:
# ["label": "<short name>", "box": [x1, y1, x2, y2]}]"""


def repair_boxes(text, min_size=5):
    """
    LLM出力から [[x1,y1,x2,y2], ...] を復元する保険関数。
    括弧の欠け・過剰・前置きテキスト・フラット出力に耐える。
    復元不能なグループは黙って捨てる(全滅なら[]を返す)。
    """
    text = text.strip()
    # コードフェンス除去
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()

    # --- 1. まず素直にパース ---
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            # [[...]] 形式ならそのまま、フラット[1,2,3,4]なら1箱として包む
            if parsed and all(isinstance(v, (int, float)) for v in parsed):
                parsed = [parsed]
            return _validate(parsed, min_size)
    except json.JSONDecodeError:
        pass

    # --- 2. 内側括弧グループから復元 ---
    # "[53, 408, 150, 500]" や "[53,408,150,500" (閉じ欠け) っぽい塊を拾う
    groups = re.findall(r"\[\s*([\d\s,.\-]+?)\s*(?:\]|$|\[)", text)
    boxes = []
    for g in groups:
        nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", g)]
        if len(nums) == 4:  # 4個ちょうどの塊だけ採用
            boxes.append(nums)
        # len 8, 12... は「区切りの ], [ が丸ごと欠けた」ケースなので分割して救済
        elif len(nums) > 4 and len(nums) % 4 == 0:
            boxes.extend([nums[i : i + 4] for i in range(0, len(nums), 4)])
        # 3個や5個は復元不能とみなして捨てる

    # --- 3. 括弧グループが取れなかったらフラット数列として救済 ---
    if not boxes:
        nums = [int(float(n)) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
        if nums and len(nums) % 4 == 0:
            boxes = [nums[i : i + 4] for i in range(0, len(nums), 4)]

    return _validate(boxes, min_size)


def _validate(boxes, min_size):
    """クランプ・座標順の正規化・退化ボックスの除去"""
    out = []
    for b in boxes:
        try:
            x1, y1, x2, y2 = (max(0, min(1000, int(v))) for v in b)
        except (ValueError, TypeError):
            continue
        if x1 > x2:
            x1, x2 = x2, x1  # 順序逆転は入れ替えて救済
        if y1 > y2:
            y1, y2 = y2, y1
        if (x2 - x1) < min_size or (y2 - y1) < min_size:
            continue  # 点みたいな退化ボックスは捨てる
        out.append([x1, y1, x2, y2])
    return out


def load_resized(path):
    img = Image.open(path).convert("RGB")
    img.thumbnail((MAX_SIZE, MAX_SIZE))
    return img


def detect(img_path):
    img = load_resized(img_path)
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=90)
    resp = client.converse(
        modelId=MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": {"format": "jpeg", "source": {"bytes": buf.getvalue()}}},
                    {"text": PROMPT},
                ],
            }
        ],
        inferenceConfig={"temperature": 0.0, "maxTokens": 2000},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    print(text)
    boxes = repair_boxes(text)
    print(boxes)
    print(type(boxes))
    # m = re.search(r"\[.*\]", text, re.DOTALL)  # JSON部分だけ抽出
    # if not m:
    if not text:
        print("!! JSON抽出失敗:\n", text)
        sys.exit(1)
    # print(m)
    return img, boxes
    # return img, json.loads(m.group(0))


def main(img_path):
    img, dets = detect(img_path)
    w, h = img.size
    out_dir = Path("crops")
    out_dir.mkdir(exist_ok=True)
    vis = img.copy()
    draw = ImageDraw.Draw(vis)
    PAD = 0.12  # クロップ余白12%(座標ズレ吸収)

    for i, d in enumerate(dets):
        print(d)
        x1, y1, x2, y2 = d
        # x1, y1, x2, y2 = d["box"]
        # [0,1000) -> pixel
        px1, py1 = x1 / 1000 * w, y1 / 1000 * h
        px2, py2 = x2 / 1000 * w, y2 / 1000 * h
        draw.rectangle([px1, py1, px2, py2], outline="red", width=3)
        # draw.text((px1, max(0, py1 - 14)), f'{i}:{d["label"]}', fill="red")
        # padded crop
        bw, bh = px2 - px1, py2 - py1
        crop = img.crop(
            (
                max(0, px1 - bw * PAD),
                max(0, py1 - bh * PAD),
                min(w, px2 + bw * PAD),
                min(h, py2 + bh * PAD),
            )
        )
        crop.save(out_dir / f"crop_{i}.jpg")
        # crop.save(out_dir / f"crop_{i}_{d['label'].replace(' ', '_')}.jpg")
        # print(f"[{i}] {d['label']}: box={d['box']}")

    vis.save("bbox_result.jpg")
    print("\n=> bbox_result.jpg と crops/ を目視チェック")


if __name__ == "__main__":
    main(sys.argv[1])

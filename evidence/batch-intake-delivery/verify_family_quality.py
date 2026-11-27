"""Evaluate fixed-threshold family suggestions against known photo ancestry.

Pass --sources pointing at skimage/data extracted from the scikit-image 0.25.2
wheel, --output pointing at a new file, and configure AI_SHIP_CLIP_MODEL_DIR.
Uses the normal import, frame-index and family-suggestion HTTP routes in a new
local test project. One request at a time, no background worker and no fitting.
"""

from argparse import ArgumentParser
from hashlib import sha256
from io import BytesIO
from itertools import combinations
import json
from pathlib import Path
import sys

from PIL import Image, ImageEnhance, __version__ as pillow_version

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.test_production import ProductionBase

PAIRED = (
    "astronaut.png",
    "camera.png",
    "rocket.jpg",
    "chelsea.png",
    "coffee.png",
    "brick.png",
    "grass.png",
    "gravel.png",
)
SINGLETONS = ("hubble_deep_field.jpg", "clock_motion.png")
THRESHOLD = 0.8
# Pins from skimage/data/_registry.py in the upstream release.
PINS = {
    "astronaut.png": "88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5",
    "camera.png": "b0793d2adda0fa6ae899c03989482bff9a42d3d5690fc7e3648f2795d730c23a",
    "rocket.jpg": "c2dd0de7c538df8d111e479619b129464d0269d0ae5fd18ca91d33a7fdfea95c",
    "chelsea.png": "596aa1e7cb875eb79f437e310381d26b338a81c2da23439704a73c4651e8c4bb",
    "coffee.png": "cc02f8ca188b167c775a7101b5d767d1e71792cf762c33d6fa15a4599b5a8de7",
    "brick.png": "7966caf324f6ba843118d98f7a07746d22f6a343430add0233eca5f6eaaa8fcf",
    "grass.png": "b6b6022426b38936c43a4ac09635cd78af074e90f42ffa8227ac8b7452d39f89",
    "gravel.png": "c48615b451bf1e606fbd72c0aa9f8cc0f068ab7111ef7d93bb9b0f2586440c12",
    "hubble_deep_field.jpg": "3a19c5dd8a927a9334bb1229a6d63711b1c0c767fb27e2286e7c84a3e2c2f5f4",
    "clock_motion.png": "f029226b28b642e80113d86622e9b215ee067a0966feaf5e60604a1e05733955",
}


def variant(blob, recipe):
    with Image.open(BytesIO(blob)) as image:
        image = image.convert("RGB")
        width, height = image.size
        if recipe == "mild":
            box = (0.12, 0.12, 0.88, 0.88)
            saturation, contrast, quality = 0.75, 1.12, 79
        else:
            box = (0.02, 0.02, 0.30, 0.30)
            saturation, contrast, quality = 0.4, 1.25, 68
        patch = image.crop(tuple(round(v * n) for v, n in zip(box, (width, height, width, height))))
        patch.thumbnail((352, 352), Image.Resampling.LANCZOS)
        patch = ImageEnhance.Color(patch).enhance(saturation)
        out = BytesIO()
        ImageEnhance.Contrast(patch).enhance(contrast).save(out, format="JPEG", quality=quality)
        return out.getvalue()


def confusion(truth, predicted, universe):
    positives = truth & universe
    found = predicted & universe
    tp, fp, fn = len(positives & found), len(found - positives), len(positives - found)
    return {
        "pairs": len(universe),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": len(universe) - tp - fp - fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def run(sources):
    case = ProductionBase()
    case.setUp()
    try:
        versions, truth_source, hashes = {}, {}, {}
        for filename in (*PAIRED, *SINGLETONS):
            blob = (sources / filename).read_bytes()
            assert sha256(blob).hexdigest() == PINS[filename], filename
            recipes = ("original", "mild", "hard") if filename in PAIRED else ("original",)
            for recipe in recipes:
                data = blob if recipe == "original" else variant(blob, recipe)
                name = Path(filename).stem + "/" + recipe
                suffix = Path(filename).suffix if recipe == "original" else ".jpg"
                digest = sha256(data).hexdigest()
                # Blind names at import. The family endpoint receives only version identifiers.
                r = case.client.post(
                    "/api/v1/media/import",
                    params={"project_id": case.pid, "filename": digest[:16] + suffix},
                    content=data,
                )
                r.raise_for_status()
                v = r.json()["data"]["version"]
                versions[v["id"]] = name
                truth_source[name], hashes[name] = filename, digest
        pages, cursor = [], ""
        while True:
            page = case.post(
                "/production/asset-index/frames/build",
                {"project_id": case.pid, "limit": 10, "after": cursor},
            )
            assert not page["errors"], page
            pages.append(page)
            cursor = page.get("next_after")
            if not cursor:
                break
        assert sum(p["frames"] for p in pages) == len(versions) == 26
        audit = case.post(
            "/production/asset-families/suggest",
            {
                "project_id": case.pid,
                "members": [{"id": item, "version": 1} for item in versions],
                "threshold": THRESHOLD,
                "request_id": "fixed-photo-pairs",
            },
        )["audit"]
        d = audit["data"]
        groups = sorted(sorted(versions[i] for i in g["members"]) for g in d["groups"])
        predicted = {pair for group in groups for pair in combinations(group, 2)}
        universe = set(combinations(sorted(truth_source), 2))
        truth = {p for p in universe if truth_source[p[0]] == truth_source[p[1]]}
        assert len(truth) == 24 and len(universe) == 325
        # Three views produce correlated pairs. Each score describes just this fixed sample.
        subtype = {}
        for left, right in combinations(("original", "mild", "hard"), 2):
            key = left + "_" + right
            sample = {p for p in universe if {n.split("/")[1] for n in p} == {left, right}}
            subtype[key] = confusion(truth, predicted, sample)
        return {
            "feature": "S04",
            "purpose": "Pairwise common photographic origin under two crop conditions",
            "threshold_fixed_before_inference": THRESHOLD,
            "algorithm": d["algorithm"],
            "model": d["space"],
            "material": "scikit-image 0.25.2 official example photos",
            "upstream": "https://github.com/scikit-image/scikit-image/tree/v0.25.2/skimage/data",
            "rights_source": "https://scikit-image.org/docs/0.25.x/api/skimage.data.html",
            "source_sha256": PINS,
            "pillow": pillow_version,
            "paired_sources": list(PAIRED),
            "unpaired_sources": list(SINGLETONS),
            "derivatives": {
                "mild": "centered 76% x 76% crop, thumbnail <=352 px, saturation 0.75, contrast 1.12, JPEG 79",
                "hard": "upper-left crop x/y=2%..30% (28% width and height), thumbnail <=352 px, saturation 0.4, contrast 1.25, JPEG 68",
            },
            "ground_truth": "Same upstream source file. Cross-source pairs are negative regardless of visual similarity. No labels or filenames given to grouping.",
            "imported_sha256": hashes,
            "index_pages": [p["frames"] for p in pages],
            "input_count": len(versions),
            "ground_truth_positive_pairs": len(truth),
            "overall": confusion(truth, predicted, universe),
            "by_view_pair": subtype,
            "suggested_clusters": groups,
            "singletons": sorted(versions[i] for i in d["unmatched"]),
            "false_positive_pairs": sorted(predicted - truth),
            "false_negative_pairs": sorted(truth - predicted),
            "limits": [
                "Only eight independently sourced positive families and two singleton sources. The 325 pairs are correlated.",
                "Still photographs and controlled derivatives only. This score is for common origin, not open-world label accuracy or product identity.",
                "The severe corner crop can remove the subject entirely. Same origin is still a positive.",
                "Default threshold fixed at 0.8. No tuning or production acceptance took place.",
            ],
        }
    finally:
        case.tearDown()


if __name__ == "__main__":
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--sources", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new report path to preserve fixed evidence")
    report = run(args.sources)
    with args.output.open("x") as output:
        json.dump(report, output, indent=2, ensure_ascii=False)
        output.write("\n")
    print(json.dumps(report["overall"]))

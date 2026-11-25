"""Measure cross-language retrieval and a revoked-source exclusion using installed real weights."""

import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.test_asset_index import AssetIndexTests


def main():
    case = AssetIndexTests()
    case.setUp()
    try:
        texts = [
            "A barista grinds roasted coffee beans before brewing espresso",
            "A mechanic changes a flat bicycle tire",
            "A hiker reaches a snow-covered mountain pass",
        ]
        versions = []
        for name in texts:
            a = case.create("Asset", {"name": name, "media_type": "video/mp4"})
            versions.append(
                case.create(
                    "AssetVersion",
                    {"asset_id": a["id"], "filename": name, "probe": {"duration": 4.0}},
                    [case.pin(a)],
                )
            )
        prepared = case.call("embed", limit=32)
        observations = []
        for q, expected in zip(
            ["制作拿铁前研磨咖啡豆", "给自行车换轮胎", "在积雪的山路上远足"], versions, strict=True
        ):
            result = case.search(q, channel="semantic")
            assert (
                result["vector_available"] and result["items"][0]["version_id"] == expected["id"]
            ), result
            top = result["items"][0]
            assert not top["matched"]
            observations.append(
                {
                    "query": q,
                    "first": top["name"],
                    "cosine": top["semantic_score"],
                    "literal_matches": top["matched"],
                }
            )
        case.change(versions[0], {"status": "revoked"})
        assert versions[0]["id"] not in [
            v["version_id"] for v in case.search("咖啡师", channel="semantic")["items"]
        ]
        print(
            json.dumps(
                {
                    "space": prepared["space"],
                    "indexed": prepared["processed"],
                    "queries": observations,
                    "revoked_result_excluded": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        case.tearDown()


if __name__ == "__main__":
    main()

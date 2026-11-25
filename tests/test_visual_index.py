"""The core application can search without installing any image model dependencies."""

import os
from unittest.mock import patch
from tests.test_production import ProductionBase


class VisualIndexTests(ProductionBase):
    def test_missing_or_invalid_weights_preserve_literal_search_and_explicit_build_status(self):
        source = self.create("Source", {"name": "test source", "rights_status": "approved"})
        asset = self.create(
            "Asset", {"name": "clip 001", "media_type": "video/mp4", "source_id": source["id"]}
        )
        version = self.create(
            "AssetVersion",
            {"filename": "known frame", "asset_id": asset["id"], "probe": {"duration": 2}},
        )
        for location in ["", str(self.temp.name)]:
            with (
                self.subTest(directory=location),
                patch.dict(os.environ, {"AI_SHIP_CLIP_MODEL_DIR": location}),
            ):
                result = self.post(
                    "/production/asset-index/query",
                    {"project_id": self.pid, "channel": "visual", "text": "known"},
                )
                self.assertEqual([r["version_id"] for r in result["items"]], [version["id"]])
                self.assertFalse(result["vector_available"])
                self.assertNotIn("frame_match", result["items"][0])
                self.assertTrue(result["fallback"])
                self.post(
                    "/production/asset-index/frames/build", {"project_id": self.pid}, expected=503
                )

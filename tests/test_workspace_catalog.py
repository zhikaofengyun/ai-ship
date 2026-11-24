import unittest
from app.catalog import catalog, workspace_catalog


class WorkspaceCatalogTests(unittest.TestCase):
    def test_interactive_projection_matches_every_control_route_and_workflow_port(self):
        full = catalog()
        ui = workspace_catalog()
        self.assertEqual(ui["source_sha256"], full["source_sha256"])
        self.assertEqual([m["id"] for m in ui["modules"]], [m["id"] for m in full["modules"]])
        self.assertEqual(len(ui["features"]), 178)
        for feature, source in zip(ui["features"], full["features"], strict=True):
            self.assertEqual(feature["id"], source["id"])
            for k in ("inputs", "outputs", "checks", "failure", "parameters"):
                self.assertEqual(feature[k], source[k])
            for k in ("inputs", "outputs", "controls"):
                self.assertEqual(feature["operation"][k], source["operation"][k])
            self.assertEqual(
                [(r["id"], r["label"]) for r in feature["routes"]],
                [(r["id"], r["label"]) for r in source["routes"]],
            )
        self.assertEqual(ui["entities"], full["entities"])
        self.assertEqual(ui["agentSystem"]["roles"], full["agentSystem"]["roles"])
        self.assertNotIn("historicalValidation", ui)

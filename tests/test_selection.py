import subprocess
from pathlib import Path
from uuid import uuid4

from app.auth import LOCAL_ACTOR
from app.media import import_saved_file
from tests.test_production import ProductionBase


def pin(r):
    return {"id": r["id"], "version": r["version"]}


class SelectionTests(ProductionBase):
    def brief_body(self, brief, **changes):
        return {
            "project_id": self.pid,
            "request_id": uuid4().hex,
            "chosen": pin(brief),
            "reason": "选用展示真实商品细节的版本",
            "purpose": "本项目测评短片",
            "expires_at": "2099-01-01T00:00:00Z",
            **changes,
        }

    def submit(self, op, data, status=200):
        response = self.client.post("/api/v1/production/selection/" + op, json=data)
        self.assertEqual(response.status_code, status, response.text)
        return response.json().get("data") or response.json()["error"]

    def test_creative_choice_retains_alternatives_and_exact_approval(self):
        p = self.create("Product", {"name": "样品", "evidence": "实拍", "SKU": "sku1"})
        chosen = self.create(
            "Brief",
            {"name": "片头 A", "intent": "开箱实录", "claims": ["样品内含一个杯子"]},
            [pin(p)],
        )
        other = self.create("Brief", {"name": "片头 B", "intent": "直接口播"})
        body = self.brief_body(
            chosen, products=[pin(p)], alternatives=[{**pin(other), "reason": "作为短版备选"}]
        )
        result = self.submit("brief", body)
        self.assertEqual(result["brief"]["version"], 2)
        approval = result["approval"]["data"]
        self.assertEqual(
            (approval["subject_id"], approval["subject_version"], approval["subject_hash"]),
            (chosen["id"], 2, result["brief"]["hash"]),
        )
        self.assertEqual(result["decision"]["data"]["options"][1]["reason"], "作为短版备选")
        self.assertEqual(self.store.get(other["id"], "local")["version"], 1)
        replay = self.submit("brief", body)
        self.assertEqual(replay["decision"]["id"], result["decision"]["id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            self.submit("brief", {**body, "reason": "不同"}, 409)["code"], "IDEMPOTENCY_CONFLICT"
        )
        self.assertEqual(len(self.store.list("local", kind="Approval")), 1)

    def test_invalid_claims_expired_foreign_and_stale_approvals_are_atomic(self):
        b = self.create("Brief", {"name": "有主张", "intent": "具体测评", "claims": ["超长续航"]})
        self.assertEqual(
            self.submit("brief", self.brief_body(b), 422)["code"], "PRODUCT_EVIDENCE_REQUIRED"
        )
        p = self.create("Product", {"SKU": "abc", "evidence": "生产记录"})
        self.assertEqual(
            self.submit(
                "brief",
                self.brief_body(b, products=[pin(p)], expires_at="2000-01-01T00:00:00Z"),
                422,
            )["code"],
            "APPROVAL_EXPIRED",
        )
        self.assertEqual(
            self.submit("brief", self.brief_body(b, chosen={"id": b["id"], "version": 2}), 409)[
                "code"
            ],
            "STALE_SELECTION",
        )
        self.assertEqual(
            self.submit(
                "brief", self.brief_body(b, alternatives=[{**pin(b), "reason": "重复"}]), 422
            )["code"],
            "DUPLICATE_OPTION",
        )
        other = self.store.create(
            "Project", {"name": "另一个项目"}, tenant_id="local", actor_id="test"
        )
        self.assertEqual(
            self.submit("brief", self.brief_body(b, project_id=other["id"]), 409)["code"],
            "PROJECT_MISMATCH",
        )
        self.assertEqual(self.store.get(b["id"], "local")["version"], 1)
        self.assertEqual(len(self.store.list("local", kind="Approval")), 0)

    def asset(self):
        path = Path(self.temp.name) / "cup.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=12",
                "-t",
                "1",
                "-threads",
                "1",
                "-c:v",
                "libx264",
                "-y",
                str(path),
            ],
            timeout=25,
            check=True,
        )
        import_saved_file(self.store, LOCAL_ACTOR, self.pid, "cup.mp4", path, "本机生成的测试素材")
        return self.store.list("local", kind="AssetVersion", project_id=self.pid)[0]

    def shot_body(self, **changes):
        return {
            "project_id": self.pid,
            "request_id": uuid4().hex,
            "plan_name": "样品分镜",
            "slot": "近景",
            "constraints": "一个杯子入画",
            "decision": "gap",
            "evidence": "等待实拍素材",
            **changes,
        }

    def test_real_shot_selection_then_locked_change_gap_and_lineage(self):
        a = self.asset()
        request = self.shot_body(
            decision="select", asset=pin(a), source_in=0.2, source_out=0.8, lock=True
        )
        result = self.submit("shot", request)
        self.assertEqual(self.submit("shot", request)["plan"]["id"], result["plan"]["id"])
        shot = self.store.get(result["slot"]["shot_ref"]["id"], "local")
        self.assertEqual(shot["data"]["asset_version_id"], a["id"])
        self.assertEqual([shot["data"]["source_in"], shot["data"]["source_out"]], [0.2, 0.8])
        plan = result["plan"]
        self.assertEqual(plan["data"]["slot_shot_refs"][0]["id"], shot["id"])
        another = self.submit("shot", self.shot_body(plan=pin(plan), slot="远景"))["plan"]
        self.assertEqual(another["data"]["slots"][0], plan["data"]["slots"][0])
        self.assertEqual(
            self.submit("shot", self.shot_body(plan=pin(plan)), 409)["code"], "STALE_SELECTION"
        )
        self.assertEqual(
            self.submit("shot", self.shot_body(plan=pin(another)), 409)["code"], "SLOT_LOCKED"
        )
        released = self.submit("shot", self.shot_body(plan=pin(another), unlock_previous=True))
        self.assertNotIn("selected_source", released["slot"])
        self.assertEqual(released["plan"]["data"]["slot_shot_refs"], [])
        self.assertEqual(
            self.store.get(plan["id"], "local", version=1)["data"]["slot_shot_refs"][0]["id"],
            shot["id"],
        )
        self.assertEqual(released["plan"]["data"]["slots"][1], another["data"]["slots"][1])

    def test_missing_bytes_out_of_range_or_unavailable_version_never_creates_plan(self):
        a = self.asset()
        invalid = self.shot_body(decision="select", asset=pin(a), source_in=0.2, source_out=2)
        self.assertEqual(self.submit("shot", invalid, 422)["code"], "SHOT_OUT_OF_RANGE")
        self.assertEqual(
            self.submit("shot", self.shot_body(asset=pin(a)), 422)["code"], "INVALID_GAP"
        )
        self.assertEqual(
            self.submit("shot", self.shot_body(decision="select"), 422)["code"],
            "SHOT_SOURCE_REQUIRED",
        )
        self.assertEqual(
            self.submit(
                "shot",
                {**invalid, "request_id": uuid4().hex, "asset": {"id": a["id"], "version": 4}},
                409,
            )["code"],
            "STALE_SHOT",
        )
        self.store.update(
            a["id"],
            {**a["data"], "status": "revoked"},
            tenant_id="local",
            actor_id="test",
            expected_revision=1,
        )
        response = self.client.post(
            "/api/v1/production/selection/shot",
            json={**invalid, "request_id": uuid4().hex, "asset": {"id": a["id"], "version": 2}},
        )
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(len(self.store.list("local", kind="Plan")), 0)

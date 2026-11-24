import hashlib
import io
import json
from pathlib import Path
import subprocess
from uuid import uuid4
import zipfile

from app.auth import LOCAL_ACTOR
from app.media import import_saved_file, render_timeline
from app.production import now
from tests.test_production import ProductionBase
from tests.test_selection import pin


class CommercialHandoffTests(ProductionBase):
    def setUp(self):
        super().setUp()
        path = Path(self.temp.name) / "cup.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=s=128x72:r=12",
                "-t",
                "1",
                "-c:v",
                "libx264",
                "-threads",
                "1",
                str(path),
            ],
            check=True,
        )
        v = import_saved_file(self.store, LOCAL_ACTOR, self.pid, path.name, path)["version"]
        timeline = self.create(
            "Timeline",
            {
                "name": "实拍成片",
                "locked": True,
                "format": {"width": 128, "height": 72, "fps": 24},
                "duration": 1,
                "clips": [
                    {
                        "asset_id": v["id"],
                        "id": "clip",
                        "source_in": 0,
                        "source_out": 1,
                        "timeline_start": 0,
                        "track": 0,
                    }
                ],
            },
            [pin(v)],
        )
        self.artifact = render_timeline(
            self.store,
            LOCAL_ACTOR,
            {"project_id": self.pid, "timeline_id": timeline["id"], "expected_revision": 1},
        )["artifact"]
        self.source = self.create(
            "Source", {"name": "商品事实凭据", "provenance": "Synthetic owner ledger for tests"}
        )
        self.creator_source = self.create(
            "Source", {"name": "账号核对凭据", "provenance": "Synthetic creator marketplace record"}
        )
        rows = self.post(
            "/production/products/import",
            {
                "project_id": self.pid,
                "source_id": self.source["id"],
                "csv_text": "SKU,price,currency,quantity,valid_until,market,shop_product_id\nCUP,12.90,USD,20,2099-04-01T00:00:00Z,US,shop-123",
            },
        )
        self.product = rows["records"][0]
        self.creator = self.create(
            "Account",
            {
                "name": "测试创作者",
                "provider": "tiktok",
                "identity_namespace": "shop_creator",
                "identity_market": "US",
                "platform_account_id": "fixture-creator",
            },
        )
        self.proofs = []
        for kind in ("content_check", "rights_check"):
            self.proofs.append(
                self.post(
                    "/production/approvals",
                    {
                        "subject_id": self.artifact["id"],
                        "subject_version": self.artifact["version"],
                        "kind": kind,
                        "decision": "approved",
                        "expires_at": "2099-04-01T00:00:00Z",
                        "evidence": "Local test artifact review",
                    },
                )
            )

    def command(self, op, status=200, **data):
        body = {"project_id": self.pid, "request_id": uuid4().hex, **data}
        result = self.client.post("/api/v1/production/commerce/" + op, json=body)
        self.assertEqual(result.status_code, status, result.text)
        return result.json().get("data") or result.json()["error"]

    def package(self, **changes):
        return self.command(
            "packages",
            **{
                "artifact": pin(self.artifact),
                "products": [pin(self.product)],
                "market": "US",
                "disclosure_kind": "paid_partnership",
                "disclosure_text": "包含付费合作",
                **changes,
            },
        )["package"]

    def pub(self, account=None):
        return self.post(
            "/production/publications",
            {
                "project_id": self.pid,
                "artifact_id": self.artifact["id"],
                "account_id": (account or self.creator)["id"],
                "title": "实拍展示",
            },
        )

    def binding(self, package, pub, account=None):
        return {
            "package": pin(package),
            "publication": pin(pub),
            "account": pin(account or self.creator),
            "market": "US",
            "creator_source": pin(self.creator_source),
            "eligibility_evidence": "已核创作者、类目与商品在该市场可售",
            "eligibility_expires_at": "2099-01-01T00:00:00Z",
        }

    def revision(self, r, **data):
        return self.store.update(
            r["id"],
            {**r["data"], **data},
            tenant_id="local",
            actor_id="owner",
            expected_revision=r["version"],
        )

    def download(self, p):
        return self.client.get(
            f"/api/v1/production/commerce/packages/{p['id']}/file", params={"version": p["version"]}
        )

    def test_accountless_package_actual_members_hashes_receipt_and_recheck(self):
        key = uuid4().hex
        pkg = self.package(request_id=key)
        self.assertEqual(self.package(request_id=key)["id"], pkg["id"])
        self.assertFalse(self.store.list("local", kind="Publication", project_id=self.pid))
        self.assertEqual(pkg["data"]["handoff_state"], "ready")
        self.assertIsNone(pkg["data"]["target_account_ref"])
        raw = self.download(pkg)
        self.assertEqual(raw.status_code, 200, raw.text[:0] if raw.status_code == 200 else raw.text)
        self.assertEqual(hashlib.sha256(raw.content).hexdigest(), pkg["data"]["checksum"])
        with zipfile.ZipFile(io.BytesIO(raw.content)) as archive:
            self.assertEqual(len(archive.namelist()), 6)
            metadata = json.loads(archive.read("commercial-metadata.json"))
            self.assertEqual(metadata["products"][0]["price"], "12.90")
            self.assertEqual(metadata["products"][0]["source"]["id"], self.source["id"])
            self.assertIn("confirm_creator_eligibility", metadata["pending_actions"])
            for file in pkg["data"]["files"]:
                actual = archive.read(file["path"])
                self.assertEqual(
                    (len(actual), hashlib.sha256(actual).hexdigest()),
                    (file["size_bytes"], file["sha256"]),
                )
        self.command(
            "receipts",
            409,
            package=pin(pkg),
            checksum="a" * 64,
            collected_at=now(),
            recipient="operator",
            evidence="checksum receipt",
        )
        received = self.command(
            "receipts",
            package=pin(pkg),
            checksum=pkg["data"]["checksum"],
            collected_at=now(),
            recipient="operator",
            evidence="checksum receipt",
        )["package"]
        self.assertEqual(received["data"]["handoff_state"], "receipt_recorded")
        self.revision(self.product, price="19.00")
        self.assertEqual(self.download(received).json()["error"]["code"], "COMMERCIAL_FACT_CHANGED")
        self.assertFalse(self.store.list("local", kind="Post", project_id=self.pid))
        replay = self.command(
            "packages",
            409,
            artifact=pin(self.artifact),
            products=[pin(self.product)],
            market="US",
            request_id=key,
            disclosure_kind="paid_partnership",
            disclosure_text="包含付费合作",
        )
        self.assertEqual(replay["code"], "COMMERCIAL_FACT_CHANGED")

    def test_manual_binding_preflight_observed_products_and_disclosure_are_separate(self):
        pkg = self.package()
        pub = self.pub()
        linked = self.command("bindings", **self.binding(pkg, pub))["publication"]
        self.assertEqual(linked["data"]["outcome"], "not_sent")
        preflight = lambda: self.client.get(
            "/api/v1/production/publications/" + pub["id"] + "/preflight"
        ).json()["data"]
        self.assertTrue(preflight()["can_handoff"])
        self.assertFalse(preflight()["can_send"])
        receipt = self.post(
            "/production/publications/" + pub["id"] + "/receipt",
            {
                "expected_revision": linked["version"],
                "platform_post_id": "fixture-post",
                "post_url": "https://example.test/video/10",
                "observed_at": now(),
                "evidence": "operator's actual post observation",
            },
        )
        post = receipt["post"]
        updated = receipt["publication"]
        self.assertNotIn("commercial_observation", post["data"])
        body = {
            "publication": pin(updated),
            "post": pin(post),
            "evidence_source": pin(self.creator_source),
            "evidence": "核对商品卡和付费标识",
            "observed_at": now(),
            "observed_shop_product_ids": [],
            "observed_disclosure_kind": "absent",
        }
        mismatch = self.command("observations", **body)["post"]
        self.assertEqual(mismatch["data"]["commercial_observation"]["status"], "mismatch")
        ok = self.command(
            "observations",
            **{
                **body,
                "post": pin(mismatch),
                "observed_at": now(),
                "observed_shop_product_ids": ["shop-123"],
                "observed_disclosure_kind": "paid_partnership",
                "observed_disclosure_text": "包含付费合作",
            },
        )["post"]
        self.assertEqual(ok["data"]["commercial_observation"]["status"], "matched_as_observed")
        self.assertEqual(ok["data"]["commercial_observation"]["basis"], "human_observation")
        self.revision(self.creator_source, status="revoked")
        self.assertFalse(preflight()["can_handoff"])
        self.assertIn("COMMERCIAL_FACT_CHANGED", [p["code"] for p in preflight()["problems"]])

    def test_stale_sources_role_market_unapproved_bytes_and_reserved_bindings(self):
        pkg = self.package()
        seller = self.create(
            "Account",
            {
                "provider": "tiktok",
                "identity_namespace": "shop_seller",
                "identity_market": "US",
                "platform_account_id": "fixture-seller",
            },
        )
        pub = self.pub(seller)
        self.assertEqual(
            self.command("bindings", 409, **self.binding(pkg, pub, seller))["code"],
            "CREATOR_SCOPE_REQUIRED",
        )
        forged = self.client.patch(
            "/api/v1/records/" + pub["id"],
            json={
                "expected_revision": pub["version"],
                "data": {**pub["data"], "commerce_binding": {"approved": True}},
            },
        )
        self.assertEqual(forged.status_code, 403)
        self.assertEqual(
            self.command(
                "packages",
                409,
                artifact=pin(self.artifact),
                products=[pin(self.product)],
                market="GB",
                disclosure_kind="own_brand",
                disclosure_text="自有品牌",
            )["code"],
            "PRODUCT_MARKET_MISMATCH",
        )
        self.post(
            "/production/approvals",
            {
                "subject_id": self.artifact["id"],
                "subject_version": self.artifact["version"],
                "kind": "rights_check",
                "decision": "rejected",
                "evidence": "right withdrawn",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )
        self.assertEqual(self.download(pkg).json()["error"]["code"], "HANDOFF_APPROVAL_REQUIRED")

    def test_readable_parent_package_and_expired_or_missing_facts(self):
        from app.media import create_package, blob_path

        parent, _ = create_package(self.store, LOCAL_ACTOR, self.artifact["id"])
        desired = {
            "source_package": pin(parent),
            "products": [pin(self.product)],
            "market": "US",
            "disclosure_kind": "own_brand",
            "disclosure_text": "自有品牌",
        }
        derived = self.command("packages", **desired)["package"]
        self.assertEqual(derived["data"]["parent_package_ref"]["id"], parent["id"])
        self.assertEqual(
            self.command("packages", 422, **desired, artifact=pin(self.artifact))["code"],
            "HANDOFF_INPUT_REQUIRED",
        )
        self.assertEqual(
            self.command(
                "bindings",
                409,
                **{
                    **self.binding(derived, self.pub()),
                    "eligibility_expires_at": "2000-01-01T00:00:00Z",
                },
            )["code"],
            "CREATOR_SCOPE_EXPIRED",
        )
        no_provenance = self.create(
            "Product",
            {
                "SKU": "SKU-X",
                "price": "3.00",
                "currency": "USD",
                "quantity": 2,
                "validity": {"valid_until": "2099-01-01T00:00:00Z"},
                "evidence": "not linked",
            },
        )
        self.assertEqual(
            self.command("packages", 409, **{**desired, "products": [pin(no_provenance)]})["code"],
            "PRODUCT_SOURCE_REQUIRED",
        )
        blob = self.store.get(parent["data"]["blob_location_id"], "local")
        blob_path(blob, "local").write_bytes(b"broken")
        self.assertEqual(self.command("packages", 409, **desired)["code"], "PACKAGE_CORRUPTION")

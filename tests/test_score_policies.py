from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from tests.test_production import ProductionBase


class ScorePolicyTests(ProductionBase):
    def call(self, action, **data):
        code = data.pop("code", 200)
        body = {"request_id": uuid4().hex, "project_id": self.pid, **data}
        return self.post("/production/score-policies/" + action, body, code)

    @staticmethod
    def pin(record):
        return {k: record[k] for k in ("id", "version")}

    def score(self, quality, other, asset=None, **extra):
        asset = asset or self.create("AssetVersion", {"name": "Fixture media"})
        dims = dict.fromkeys(
            ("relevance", "fatigue", "confidence", "tag_completeness", "performance"), other
        )
        return self.post(
            "/production/assets/score",
            {
                "project_id": self.pid,
                "asset_version_id": asset["id"],
                "dimensions": {"quality": quality, **dims},
                **extra,
            },
        )

    def propose(self, expected=0, **extra):
        return self.call(
            "propose",
            name="画质优先",
            hypothesis="经人工复核，高画质素材应排在前面。",
            weights={"quality": 1},
            expected_pointer_version=expected,
            **extra,
        )

    def cases(self):
        return [
            {
                "preferred": self.pin(self.score(0.9, 0.1)),
                "comparison": self.pin(self.score(0.25, 0.8)),
                "reason": "实际查看该组成对镜头，前者清晰。",
            }
            for _ in range(2)
        ]

    def approved(self):
        cand = self.propose()
        pairs = self.cases()
        exp = self.call("evaluate", candidate=self.pin(cand), cases=pairs)
        self.assertTrue(exp["data"]["result"]["passed"])
        approval = self.call(
            "approve",
            candidate=self.pin(cand),
            evaluation=self.pin(exp),
            expires_at="2099-01-01T00:00:00Z",
            reason="仅限本项目素材排序",
        )
        return cand, pairs, exp, approval

    def test_limited_trial_sticky_assignment_counts_history_stop_and_stale_guard(self):
        from app.production import digest

        candidate, cases, evaluation, approval = self.approved()
        body = {
            "approval": self.pin(approval),
            "percent": 15,
            "expected_trial_version": 0,
            "stop_condition": "停用任一训练样本或复核发现排序退化",
        }
        key = uuid4().hex
        trial = self.call("trials/start", request_id=key, **body)
        self.assertEqual(self.call("trials/start", request_id=key, **body)["id"], trial["id"])
        self.call("trials/start", code=409, **body)
        read = lambda tail="": self.client.get(
            f"/api/v1/production/score-policies/{self.pid}/trial" + tail
        ).json()["data"]
        self.assertEqual(
            self.call("activate", approval=self.pin(approval), code=409)["error"]["code"],
            "TRIAL_REVIEW_REQUIRED",
        )
        self.assertEqual(
            self.call(
                "trials/stop",
                expected_trial_version=1,
                decision="recommend_release",
                reason="过早",
                code=409,
            )["error"]["code"],
            "TRIAL_OBSERVATIONS_REQUIRED",
        )
        self.assertEqual(read()["arms"], {})
        arms = {}
        for n in range(1000):
            unit = f"camera-{n}"
            bucket = int(digest([trial["data"]["assignment_salt"], unit])[:16], 16) % 100
            arms.setdefault("candidate" if bucket < 15 else "baseline", unit)
            if len(arms) == 2:
                break
        for arm, unit in arms.items():
            self.store.create(
                "Asset", {"name": unit}, tenant_id="local", project_id=self.pid, record_id=unit
            )
            item = self.create("AssetVersion", {"name": "recorded take", "asset_id": unit})
            twin = self.create("AssetVersion", {"name": "another version", "asset_id": unit})
            score_key = uuid4().hex
            a = self.score(0.9, 0.1, asset=item, request_id=score_key)
            again = self.score(0.9, 0.1, asset=item, request_id=score_key)
            self.assertTrue(again["idempotent_replay"])
            self.assertEqual(again["id"], a["id"])
            self.post(
                "/production/assets/score",
                {
                    "project_id": self.pid,
                    "asset_version_id": item["id"],
                    "request_id": score_key,
                    "dimensions": {"quality": 0.1},
                },
                409,
            )
            b = self.score(0.9, 0.1, asset=twin)
            self.assertEqual(a["data"]["trial_assignment"], b["data"]["trial_assignment"])
            self.assertEqual(a["data"]["trial_assignment"]["arm"], arm)
            self.assertEqual(
                a["data"]["score_policy"]["id"],
                candidate["id"] if arm == "candidate" else candidate["data"]["parent"]["id"],
            )
            self.assertAlmostEqual(
                a["data"]["observed_score"], 0.9 if arm == "candidate" else (0.9 + 5 * 0.1) / 6
            )
        self.score(0.9, 0.1, asset=item, weights={"relevance": 1})
        summary = read()["arms"]
        for arm in arms:
            self.assertEqual(
                (summary[arm]["units"], summary[arm]["scores"], summary[arm]["measured"]), (1, 2, 2)
            )
        stopped = self.call(
            "trials/stop", expected_trial_version=trial["version"], reason="检查首轮"
        )
        self.assertEqual(stopped["data"]["status"], "stopped")
        self.assertEqual(stopped["data"]["reviewed_arms"], summary)
        self.assertEqual(
            self.call("activate", approval=self.pin(approval), code=409)["error"]["code"],
            "TRIAL_REVIEW_REQUIRED",
        )
        self.assertNotIn("trial_assignment", self.score(0.9, 0.1, asset=item)["data"])
        self.assertEqual(read()["arms"], summary)
        trial2 = self.call("trials/start", **{**body, "expected_trial_version": stopped["version"]})
        self.assertEqual(read()["arms"], {})
        self.assertEqual(read("?version=1")["arms"], summary)
        sample = self.store.get(cases[0]["preferred"]["id"], "local")
        self.store.update(
            sample["id"],
            {**sample["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=1,
        )
        self.assertTrue(read()["ineligible_reason"])
        next_score = self.score(0.9, 0.1, asset=item)
        self.assertNotIn("trial_assignment", next_score["data"])
        self.assertEqual(read()["trial"]["data"]["status"], "stopped")
        self.assertEqual(read()["trial"]["version"], trial2["version"] + 1)
        self.assertEqual(read("?version=1")["arms"], summary)

    def test_freeze_activate_consume_replay_and_rollback(self):
        previous = self.score(0.9, 0.1)
        self.assertIsNone(previous["data"]["score_policy"])
        cand, pairs, exp, approval = self.approved()
        self.assertAlmostEqual(
            self.score(0.9, 0.1)["data"]["observed_score"], previous["data"]["observed_score"]
        )
        request = uuid4().hex
        ptr = self.call("activate", approval=self.pin(approval), request_id=request)
        again = self.call("activate", approval=self.pin(approval), request_id=request)
        self.assertTrue(again.pop("idempotent_replay"))
        self.assertEqual(ptr, again)
        active = self.score(0.9, 0.1)
        self.assertEqual(active["data"]["score_policy"]["id"], cand["id"])
        self.assertAlmostEqual(active["data"]["observed_score"], 0.9)
        manual = self.score(0.9, 0.1, weights={"relevance": 1})
        self.assertAlmostEqual(manual["data"]["observed_score"], 0.1)
        self.assertIsNone(manual["data"]["score_policy"])
        self.call("activate", approval=self.pin(approval), code=409)
        rolled = self.call(
            "rollback",
            expected_pointer_version=ptr["version"],
            evidence=self.pin(exp),
            reason="本轮样本不覆盖下一批素材",
        )
        self.assertEqual(rolled["pointer"]["data"]["active"], cand["data"]["parent"])
        self.assertAlmostEqual(
            self.score(0.9, 0.1)["data"]["observed_score"], previous["data"]["observed_score"]
        )
        stored = self.store.get(active["id"], "local")
        self.assertEqual(active, stored)
        self.assertEqual(
            [self.pin(approval)], [self.pin(r) for r in ptr["refs"] if r["kind"] == "Approval"]
        )
        self.assertEqual(
            pairs,
            [
                {k: c[k] for k in ("preferred", "comparison", "reason")}
                for c in exp["data"]["result"]["cases"]
            ],
        )

    def test_fit_draft_holdout_and_production_release(self):
        training, held_out = self.cases(), self.cases()
        fitted = self.call(
            "optimize",
            training_cases=training,
            name="质量权重回归",
            hypothesis="清晰度更接近此批人工顺序",
            expected_pointer_version=0,
        )
        self.assertEqual(fitted["data"]["optimizer"]["evaluations"], 25)
        self.assertEqual(fitted["data"]["optimizer"]["candidate_correct"], 2)
        self.assertEqual(fitted["data"]["release_state"], "draft")
        self.assertEqual(
            self.call("evaluate", candidate=self.pin(fitted), cases=training, code=422)["error"][
                "code"
            ],
            "DUPLICATE_SAMPLE",
        )
        evaluated = self.call("evaluate", candidate=self.pin(fitted), cases=held_out)
        self.assertTrue(evaluated["data"]["result"]["passed"])
        proof = self.call(
            "approve",
            candidate=self.pin(fitted),
            evaluation=self.pin(evaluated),
            expires_at="2099-01-01T00:00:00Z",
            reason="独立样本审阅",
        )
        ptr = self.call("activate", approval=self.pin(proof))
        self.assertEqual(ptr["data"]["active"]["id"], fitted["id"])
        consumed = self.score(0.7, 0.5)
        self.assertEqual(consumed["data"]["score_policy"]["id"], fitted["id"])
        row = self.store.get(training[0]["preferred"]["id"], "local")
        self.store.update(
            row["id"],
            {**row["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=row["version"],
        )
        self.assertNotEqual(self.score(0.7, 0.5)["data"]["score_policy"]["id"], fitted["id"])

    def test_incomplete_duplicate_or_failed_case_blocks_approval(self):
        cand = self.propose()
        cases = self.cases()
        self.call("evaluate", candidate=self.pin(cand), cases=[cases[0], cases[0]], code=422)
        cases[1]["preferred"], cases[1]["comparison"] = (
            cases[1]["comparison"],
            cases[1]["preferred"],
        )
        exp = self.call("evaluate", candidate=self.pin(cand), cases=cases)
        self.assertFalse(exp["data"]["result"]["passed"])
        self.call(
            "approve",
            candidate=self.pin(cand),
            evaluation=self.pin(exp),
            expires_at="2099-01-01T00:00:00Z",
            reason="仍坚持",
            code=409,
        )
        cases[1]["preferred"] = self.pin(self.score(0.99, None))
        incomplete = self.call("evaluate", candidate=self.pin(cand), cases=cases)
        self.assertFalse(incomplete["data"]["result"]["passed"])
        self.assertFalse(incomplete["data"]["result"]["cases"][1]["baseline"]["complete"])

    def test_forgery_staleness_and_project_scope(self):
        cand, pairs, exp, approval = self.approved()
        endpoint = "/api/v1/records/" + approval["id"]
        changed = self.client.patch(
            endpoint, json={"expected_revision": 1, "data": {"name": "rewrite"}, "reason": "test"}
        )
        self.assertEqual(changed.status_code, 403)
        fake = self.create("Experiment", {"name": "generic", "result": {"passed": True}})
        self.call(
            "approve",
            candidate=self.pin(cand),
            evaluation=self.pin(fake),
            expires_at="2099-01-01T00:00:00Z",
            reason="伪造回归",
            code=409,
        )
        self.call(
            "approve",
            candidate=self.pin(cand),
            evaluation=self.pin(exp),
            expires_at="2000-01-01T00:00:00Z",
            reason="已过期",
            code=409,
        )
        other = self.create("Project", {"name": "Other"})
        self.call("activate", approval=self.pin(approval), project_id=other["id"], code=409)
        row = self.store.get(pairs[0]["preferred"]["id"], "local")
        self.store.update(
            row["id"],
            {**row["data"], "status": "restricted"},
            tenant_id="local",
            expected_revision=1,
        )
        self.call("activate", approval=self.pin(approval), code=409)
        self.assertEqual(self.store.get(cand["id"], "local"), cand)

    def test_pause_blocks_policy_scores_and_role_gate(self):
        cand, _, _, approval = self.approved()
        from app.auth import current_actor, Actor
        from app.main import app

        app.dependency_overrides[current_actor] = lambda: Actor("local", "worker", "worker")
        try:
            self.call("activate", approval=self.pin(approval), code=403)
        finally:
            del app.dependency_overrides[current_actor]
        ptr = self.call("activate", approval=self.pin(approval))
        paused = self.call(
            "rollback", expected_pointer_version=ptr["version"], reason="紧急暂停", pause_only=True
        )
        asset = self.create("AssetVersion", {"name": "other"})
        self.post(
            "/production/assets/score",
            {
                "project_id": self.pid,
                "asset_version_id": asset["id"],
                "dimensions": {"quality": 0.9},
            },
            409,
        )
        self.call(
            "rollback",
            expected_pointer_version=paused["pointer"]["version"],
            reason="当前核验允许恢复上一基线",
        )
        self.assertNotEqual(self.score(0.9, 0.1)["data"]["score_policy"]["id"], cand["id"])

    def test_changed_sample_automatically_rolls_back_before_next_score(self):
        cand, pairs, _, approval = self.approved()
        self.call("activate", approval=self.pin(approval))
        score_id = pairs[0]["preferred"]["id"]
        old = self.store.get(score_id, "local")
        self.store.update(
            score_id, {**old["data"], "status": "revoked"}, tenant_id="local", expected_revision=1
        )
        new = self.score(0.9, 0.1)
        self.assertEqual(new["data"]["score_policy"]["id"], cand["data"]["parent"]["id"])
        ptr = self.client.get("/api/v1/production/score-policies/" + self.pid).json()["data"][
            "pointer"
        ]
        decision = self.store.get(ptr["data"]["rollback_decision"]["id"], "local")
        self.assertEqual(decision["data"]["trigger"], "release_evidence_changed")
        self.assertEqual(ptr["version"], 3)
        self.score(0.9, 0.1)
        self.assertEqual(self.store.get(ptr["id"], "local")["version"], 3)

    def test_concurrent_activation_commits_one_pointer_revision(self):
        _, _, _, approval = self.approved()

        def activate(_):
            r = self.client.post(
                "/api/v1/production/score-policies/activate",
                json={
                    "project_id": self.pid,
                    "request_id": uuid4().hex,
                    "approval": self.pin(approval),
                },
            )
            return r.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(activate, range(2))), [200, 409])

from uuid import uuid4

from tests.test_production import ProductionBase


def pin(item):
    return {"id": item["id"], "version": item["version"]}


def proposal(name, hook, stages, shots, sound):
    return {
        "name": name,
        "hook": hook,
        "narrative": stages,
        "shot_families": shots,
        "sound": sound,
        "script": "A practical example of " + name,
        "hypothesis": "观众能否理解使用条件",
        "feasibility": "现有实拍素材，需复核商品主张",
    }


class CreativeTests(ProductionBase):
    def submit(self, endpoint, data, expected=200):
        return self.post("/production/creative/" + endpoint, data, expected)

    def common(self):
        return {"project_id": self.pid, "request_id": uuid4().hex}

    def topic_body(self, **kw):
        return {
            **self.common(),
            "project": pin(self.project),
            "topic": "通勤杯子",
            "audience": "通勤用户",
            "rationale": "通勤使用场景需要展示容量和收纳",
            **kw,
        }

    def test_scoped_match_versions_and_evergreen_fallback(self):
        trend = self.post(
            "/production/trends",
            {
                "project_id": self.pid,
                "title": "通勤测评",
                "canonical_url": "https://example.test/observation",
                "evidence": "可核查的观察",
                "region": "HK",
                "platform": "manual",
                "observed_at": "2025-02-20T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "observations": {"views": 10},
            },
        )
        product = self.create("Product", {"SKU": "CUP", "name": "杯子", "evidence": "样品图"})
        matched = self.submit("match", self.topic_body(trend=pin(trend), product=pin(product)))
        fit = matched["data"]["match_evidence"]
        self.assertIn("通勤", fit["matched_terms"]["audience"])
        self.assertEqual(fit["trend_observations"], {"views": 10})
        self.assertIsNone(fit["popularity"])
        self.assertEqual(
            {r["kind"] for r in matched["refs"]}, {"Project", "Source", "Trend", "Product"}
        )
        self.store.update(
            trend["id"],
            {**trend["data"], "status": "revoked"},
            tenant_id="local",
            expected_revision=1,
            actor_id="test",
        )
        denied = self.submit("match", self.topic_body(trend=pin(trend)), 409)
        self.assertEqual(denied["error"]["code"], "STALE_SELECTION")
        evergreen = self.submit("match", self.topic_body())
        self.assertEqual(evergreen["data"]["topic_mode"], "evergreen")
        self.assertIsNone(evergreen["data"]["match_evidence"]["trend_observations"])
        self.assertEqual([r["kind"] for r in evergreen["refs"]], ["Project"])

    def test_director_proposals_comparison_and_approval_share_pinned_facts(self):
        product = self.create("Product", {"SKU": "CUP", "evidence": "商品图"})
        topic = self.submit("match", self.topic_body(product=pin(product)))
        first = proposal("演示", "使用结果", ["问题", "演示"], ["外观", "盖子"], "环境音")
        other = proposal("测量", "提出问题", ["测量", "测后展示"], ["尺寸", "盖子"], "口播")
        malformed = {**first, "name": "换称呼", "narrative": list(reversed(first["narrative"]))}
        with_bad = {**self.common(), "brief": pin(topic), "proposals": [first, other, malformed]}
        self.assertEqual(
            self.submit("directions", with_bad, 422)["error"]["code"], "DIRECTIONS_TOO_SIMILAR"
        )
        self.assertFalse(self.store.list("local", kind="Script"))
        body = {**self.common(), "brief": pin(topic), "proposals": [first, other]}
        created = self.submit("directions", body)
        briefs = [d["brief"] for d in created["directions"]]
        replay = self.submit("directions", body)
        self.assertEqual(
            [d["brief"]["id"] for d in replay["directions"]], [b["id"] for b in briefs]
        )
        self.assertEqual(len(self.store.list("local", kind="Script")), 2)
        checked = self.submit(
            "preflight",
            {
                **self.common(),
                "brief": pin(briefs[0]),
                "comparisons": [pin(briefs[1])],
                "threshold": 0.5,
            },
        )
        match = checked["data"]["evidence"][0]
        self.assertEqual(match["scores"]["shot_families"], 0.5)
        self.assertIn(
            {"dimension": "shot_families", "index": 1, "value": "盖子"}, match["repeated_locations"]
        )
        self.assertIn("shot_families", match["review_dimensions"])
        self.assertEqual(checked["data"]["coverage"], "selected_structures")
        incomplete = self.submit(
            "preflight", {**self.common(), "brief": pin(briefs[0]), "comparisons": [pin(topic)]}
        )
        self.assertEqual(incomplete["data"]["coverage"], "insufficient_history")
        denied = self.submit(
            "preflight",
            {**self.common(), "brief": pin(briefs[0]), "comparisons": [pin(briefs[0])]},
            422,
        )
        self.assertEqual(denied["error"]["code"], "DUPLICATE_COMPARISON")
        approved = self.post(
            "/production/selection/brief",
            {
                **self.common(),
                "chosen": pin(briefs[0]),
                "alternatives": [{**pin(briefs[1]), "reason": "备选"}],
                "products": [pin(product)],
                "reason": "使用已查实的规格",
                "purpose": "产品演示",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )
        self.assertEqual(approved["brief"]["data"]["status"], "approved")
        self.store.update(
            product["id"],
            {**product["data"], "evidence": "新图片"},
            tenant_id="local",
            expected_revision=1,
            actor_id="test",
        )
        rejected = self.submit("directions", {**body, "request_id": uuid4().hex}, 409)
        self.assertEqual(rejected["error"]["code"], "CREATIVE_FACT_CHANGED")

    def test_reposted_sources_conflicts_expiry_and_hypothesis_gate(self):
        trend = self.post(
            "/production/trends",
            {
                "project_id": self.pid,
                "title": "通勤",
                "canonical_url": "https://example.test/news",
                "evidence": "现场观察",
                "platform": "manual",
                "observed_at": "2025-02-20T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        )
        sources = [self.store.get(trend["data"]["raw_ref"], "local")] + [
            self.create("Source", {"canonical_url": u})
            for u in ("https://syndicate.test/repost", "https://other.test/field")
        ]

        def obs(i, origin, publisher, stance="supports"):
            return {
                "source": pin(sources[i]),
                "original_url": origin,
                "publisher_group": publisher,
                "published_at": "2025-02-01T10:00:00Z",
                "observed_at": "2025-02-20T00:00:00Z",
                "stance": stance,
                "note": "人工读回具体事件并记录转载关系",
            }

        def verify(last, evidence):
            return self.submit(
                "verify-trend", {**self.common(), "trend": pin(last), "evidence": evidence}
            )

        rows = [
            obs(0, "https://example.test/news", "原始编辑部"),
            obs(1, "https://Example.test/news?utm_source=share", "通讯社"),
        ]
        one = verify(trend, rows)
        self.assertEqual(one["data"]["source_review"]["supporting_groups"], 1)
        self.assertEqual(one["data"]["verification"], "insufficient_independent_sources")
        self.assertEqual(
            self.submit("match", self.topic_body(trend=pin(one)), 409)["error"]["code"],
            "TREND_REVIEW_REQUIRED",
        )
        hypothesis = self.submit("match", self.topic_body(trend=pin(one), allow_hypothesis=True))
        self.assertIn("待证", hypothesis["data"]["match_evidence"]["risks"][0])
        r = obs(2, "https://other.test/field", "独立采编")
        historical = self.create(
            "Source",
            {"canonical_url": "https://background.test/z", "expires_at": "2024-01-01T12:00:00Z"},
        )
        sources.append(historical)
        context = obs(3, "https://background.test/z", "背景存档", "context")
        two = verify(one, [*rows, r, context])
        self.assertEqual(two["data"]["verification"], "corroborated_as_reviewed")
        fit = self.submit("match", self.topic_body(trend=pin(two)))
        self.assertNotIn(historical["id"], [p["id"] for p in fit["refs"]])
        raw = self.client.post(
            "/api/v1/records",
            json={
                "kind": "Trend",
                "project_id": self.pid,
                "data": {"verification": "corroborated_as_reviewed"},
            },
        )
        self.assertEqual(raw.status_code, 403)
        conflict = verify(two, [*rows, {**r, "stance": "contradicts"}])
        self.assertEqual(conflict["data"]["verification"], "conflicting")
        old = self.store.update(
            trend["id"],
            {**conflict["data"], "expires_at": "2025-02-28T00:00:00Z"},
            tenant_id="local",
            expected_revision=conflict["version"],
            actor_id="test",
        )
        expired = verify(old, rows)
        self.assertEqual(expired["data"]["source_review"]["outcome"], "expired")
        self.assertEqual(
            self.submit("match", self.topic_body(trend=pin(expired), allow_hypothesis=True), 409)[
                "error"
            ]["code"],
            "DEPENDENCY_NOT_CURRENT",
        )
        self.assertEqual(self.submit("match", self.topic_body())["data"]["topic_mode"], "evergreen")

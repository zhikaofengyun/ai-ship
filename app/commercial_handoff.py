"""Atlas F03 manual commercial handoff, local delivery and per-post observations."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import Field

from .api import ok
from .auth import Actor, actor_still_authorized, current_actor, require_approval, require_write
from .errors import DomainError
from .execution import _verify_delivery_package
from .media import blob_path, check_current_media_rights, create_package, resolve_media
from .production import (
    Body,
    current_dependency_problem,
    digest,
    now,
    record,
    ref,
    revise,
    save,
    timestamp,
)
from .selection import ChosenVersion, Label, Note, on_request
from .store import get_store

router = APIRouter(prefix="/production/commerce", tags=["商品交接"])


class Command(Body):
    project_id: Label
    request_id: Label


class Prepare(Command):
    artifact: ChosenVersion | None = None
    source_package: ChosenVersion | None = None
    products: list[ChosenVersion] = Field(min_length=1, max_length=30)
    account: ChosenVersion | None = None
    market: Label | None = None
    disclosure_kind: Literal["own_brand", "paid_partnership", "own_brand_and_paid_partnership"]
    disclosure_text: Note


def pinned(actor, pid, pin, kind):
    rid = pin["id"] if isinstance(pin, dict) else pin.id
    version = pin["version"] if isinstance(pin, dict) else pin.version
    row = record(actor, rid, kind, project=pid)
    if row["version"] != version:
        raise DomainError(
            "COMMERCIAL_FACT_CHANGED", f"{kind} 版本已变化，请重新核对并制作新交接包。", 409
        )
    if problem := current_dependency_problem(row):
        raise DomainError(problem["code"], problem["message"], 409)
    return row


def source_evidence(actor, pid, pin):
    source = pinned(actor, pid, pin, "Source")
    check_current_media_rights(get_store(), actor, source, "export")
    if not any(
        source["data"].get(k) for k in ("provenance", "canonical_url", "evidence", "original_url")
    ):
        raise DomainError("PRODUCT_SOURCE_REQUIRED", "来源记录缺少可核对的出处或凭据。", 409)
    return source


def product_fact(actor, pid, pin, market):
    product = pinned(actor, pid, pin, "Product")
    d = product["data"]
    src = d.get("evidence_source_id")
    matched = next((r for r in product["refs"] if r["kind"] == "Source" and r["id"] == src), None)
    if not matched or not d.get("evidence"):
        raise DomainError("PRODUCT_SOURCE_REQUIRED", "商品需绑定有证据的来源版本。", 409)
    source = source_evidence(actor, pid, matched)
    valid = (d.get("validity") or {}).get("valid_until") or d.get("valid_until")
    if not valid or timestamp(valid) <= datetime.now(timezone.utc):
        raise DomainError("PRODUCT_VALIDITY_REQUIRED", "商品缺少当前可核实的有效期。", 409)
    try:
        price = Decimal(str(d.get("price")))
        quantity = d.get("quantity")
        if isinstance(quantity, float) and quantity.is_integer():
            quantity = int(quantity)
        if (
            not price.is_finite()
            or price < 0
            or type(quantity) is not int
            or quantity < 0
            or not re.fullmatch(r"[A-Z]{3}", str(d.get("currency", "")))
            or not str(d.get("SKU", "")).strip()
        ):
            raise ValueError()
    except (ValueError, InvalidOperation):
        raise DomainError(
            "PRODUCT_FACTS_REQUIRED", "需要完整的 SKU、非负价格、币种和已核数量。", 409
        )
    if market and d.get("market") and str(d["market"]).casefold() != market.casefold():
        raise DomainError("PRODUCT_MARKET_MISMATCH", "商品市场与目标市场不符。", 409)
    return {
        "product": ref(product),
        "source": ref(source),
        "SKU": d["SKU"],
        "name": d.get("name") or d["SKU"],
        "price": str(price),
        "currency": d["currency"],
        "quantity": quantity,
        "valid_until": valid,
        "evidence": d["evidence"],
        "market": d.get("market"),
        "shop_product_id": d.get("shop_product_id"),
    }, source


def artifact_reviews(actor, pid, artifact, account=None):
    _, _, path = resolve_media(get_store(), actor, artifact["id"], project_id=pid, purpose="export")
    if artifact["data"].get("status") not in {"technical_passed", "qc_passed"} and not (
        artifact["data"].get("quality_report") or {}
    ).get("technical_passed"):
        raise DomainError("MEDIA_QC_REQUIRED", "商品交接须使用通过技术质检的成片。", 409)
    with get_store().connection() as conn:
        rows = conn.execute(
            """SELECT r.id,r.kind,r.project_id,r.tenant_id,v.* FROM records r JOIN revisions v
          ON r.tenant_id=v.tenant_id AND r.id=v.record_id AND r.latest_version=v.version
          WHERE r.tenant_id=? AND r.project_id=? AND r.kind='Approval' AND json_extract(v.data_json,'$.subject_id')=?
          ORDER BY v.created_at DESC,r.id DESC""",
            (actor.tenant_id, pid, artifact["id"]),
        )
        seen = {}
        for raw in rows:
            a = get_store()._row_record(raw)
            d = a["data"]
            if (
                d.get("subject_version") == artifact["version"]
                and d.get("subject_hash") == artifact["hash"]
                and d.get("operation") in {"review", "commercial_handoff"}
                and (not d.get("account_id") or d["account_id"] == account)
            ):
                seen.setdefault(d.get("kind"), a)
    refs = []
    for kind in ("content_check", "rights_check"):
        review = seen.get(kind)
        if (
            not review
            or review["data"].get("decision") != "approved"
            or timestamp(review["data"].get("expires_at", "")) <= datetime.now(timezone.utc)
        ):
            raise DomainError(
                "HANDOFF_APPROVAL_REQUIRED", "成片须具备有效且范围相符的内容与权利审阅。", 409
            )
        refs.append(ref(review))
    return refs


def account_market(account, market):
    actual = account["data"].get("identity_market") or account["data"].get("region")
    if market and actual and str(actual).casefold() != market.casefold():
        raise DomainError("ACCOUNT_MARKET_MISMATCH", "目标市场与账号已登记市场不同。", 409)
    return actual


@router.post("/packages")
def prepare(body: Prepare, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        pid = body.project_id
        if bool(body.artifact) == bool(body.source_package):
            raise DomainError("HANDOFF_INPUT_REQUIRED", "请选择具体成片或已有可读交付包中的一个。")
        parent = None
        if body.source_package:
            parent = pinned(actor, pid, body.source_package, "DeliveryPackage")
            _verify_delivery_package(get_store(), actor, parent, pid)
            parent_pin = next(
                (
                    r
                    for r in parent["refs"]
                    if r["kind"] == "Artifact" and r["id"] == parent["data"].get("artifact_id")
                ),
                None,
            )
            if not parent_pin:
                raise DomainError("PACKAGE_CONTRACT", "原交付包缺少可核对的成片引用。", 409)
            artifact = pinned(actor, pid, parent_pin, "Artifact")
        else:
            artifact = pinned(actor, pid, body.artifact, "Artifact")
        account = pinned(actor, pid, body.account, "Account") if body.account else None
        if account:
            account_market(account, body.market)
        approvals = artifact_reviews(actor, pid, artifact, account["id"] if account else None)
        products = [product_fact(actor, pid, p, body.market)[0] for p in body.products]
        if len({p["SKU"] for p in products}) != len(products):
            raise DomainError("DUPLICATE_PRODUCT", "同一交接包不能重复绑定 SKU。")
        pending = [
            "confirm_creator_eligibility",
            "attach_products_in_platform",
            "verify_final_post_and_disclosures",
        ]
        if not account:
            pending.insert(0, "select_target_account")
        if not body.market:
            pending.insert(0, "confirm_target_market")
        if any(p["quantity"] == 0 for p in products):
            pending.append("recheck_stock")
        if any(not p["shop_product_id"] for p in products):
            pending.append("resolve_platform_product_ids")
        snapshot = {
            "artifact": ref(artifact),
            "parent_package": ref(parent) if parent else None,
            "products": products,
            "account": ref(account) if account else None,
            "market": body.market,
            "disclosure": {"kind": body.disclosure_kind, "text": body.disclosure_text},
            "approval_refs": approvals,
            "pending_actions": pending,
            "product_attachment": "unverified",
            "publication": "not_sent",
        }
        refs = [
            *approvals,
            *([ref(parent)] if parent else []),
            *([ref(account)] if account else []),
            *[r for p in products for r in (p["product"], p["source"])],
        ]
        package, _ = create_package(
            get_store(), actor, artifact["id"], commerce=snapshot, commerce_refs=refs
        )
        if not actor_still_authorized(actor):
            raise DomainError("IDENTITY_REVOKED", "打包期间操作权限已变化。", 403)
        verify_package(actor, package, check_archive=True)
        return {"package": package}

    result = on_request("commercial-handoff-package", body, actor, apply)
    if result.get("idempotent_replay"):
        current = record(actor, result["package"]["id"], "DeliveryPackage", project=body.project_id)
        verify_package(actor, current, check_archive=True)
        result["package"] = current
    return ok(result, request)


def verify_package(actor, package, *, check_archive):
    d = package["data"]
    if d.get("purpose") != "publish_handoff" or not isinstance(d.get("commercial_metadata"), dict):
        raise DomainError("WRONG_PACKAGE_PURPOSE", "请选择已核验的商品交接包。", 409)
    snap = d["commercial_metadata"]
    pid = package["project_id"]
    asset = pinned(actor, pid, snap["artifact"], "Artifact")
    account = pinned(actor, pid, snap["account"], "Account") if snap["account"] else None
    if account:
        account_market(account, snap["market"])
    artifact_reviews(actor, pid, asset, account["id"] if account else None)
    for old in snap["products"]:
        fact, _ = product_fact(actor, pid, old["product"], snap["market"])
        if fact != old:
            raise DomainError("COMMERCIAL_FACT_CHANGED", "商品事实与交接快照不一致。", 409)
    if check_archive:
        _verify_delivery_package(get_store(), actor, package, pid)
    return snap


@router.get("/packages/{package_id}/file")
def download(package_id: str, version: int, actor: Actor = Depends(current_actor)):
    pkg = record(actor, package_id, "DeliveryPackage")
    if pkg["version"] != version:
        raise DomainError("PACKAGE_VERSION_CHANGED", "交接包记录已更新，请刷新。", 409)
    verify_package(actor, pkg, check_archive=True)
    blob = record(actor, pkg["data"]["blob_location_id"], "BlobLocation", project=pkg["project_id"])
    return FileResponse(
        blob_path(blob, actor.tenant_id),
        media_type="application/zip",
        filename="commercial-handoff.zip",
    )


class Bind(Command):
    package: ChosenVersion
    publication: ChosenVersion
    account: ChosenVersion
    market: Label
    creator_source: ChosenVersion
    eligibility_evidence: Note
    eligibility_expires_at: str


def valid_creator(actor, pid, binding):
    account = pinned(actor, pid, binding["account"], "Account")
    source = source_evidence(actor, pid, binding["creator_source"])
    identity = account["data"]
    if (
        identity.get("identity_namespace") != "shop_creator"
        or not (
            identity.get("platform_account_id")
            or identity.get("platform_subject")
            or identity.get("external_identity_key")
        )
        or not account_market(account, binding["market"])
    ):
        raise DomainError(
            "CREATOR_SCOPE_REQUIRED",
            "需要当前市场的创作者账号；卖家、店铺或运营账号不能代替。",
            409,
        )
    if timestamp(binding["eligibility_expires_at"]) <= datetime.now(timezone.utc):
        raise DomainError("CREATOR_SCOPE_EXPIRED", "创作者资格与商品限制核对已到期。", 409)
    return source


def verify_bound_metadata(data, snap):
    binding = data["commerce_binding"]
    if (
        binding["account"]["id"] != data.get("account_id")
        or snap["artifact"]["id"] != data.get("artifact_id")
        or snap["artifact"]["version"] != data.get("artifact_version")
        or binding["metadata_hash"] != digest(snap)
        or binding["approved_metadata_hash"] != digest(data.get("metadata"))
        or data.get("metadata_hash") != digest(data.get("metadata"))
    ):
        raise DomainError("COMMERCIAL_BINDING_CHANGED", "发布对象或元数据与核验绑定不一致。", 409)


def verify_binding(actor, publication):
    pid = publication["project_id"]
    data = publication["data"]
    binding = data["commerce_binding"]
    pkg = record(actor, binding["package"]["id"], "DeliveryPackage", project=pid)
    snap = verify_package(actor, pkg, check_archive=False)
    artifact = pinned(actor, pid, snap["artifact"], "Artifact")
    artifact_reviews(actor, pid, artifact, binding["account"]["id"])
    valid_creator(actor, pid, binding)
    verify_bound_metadata(data, snap)
    return snap


@router.post("/bindings")
def bind(body: Bind, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        pid = body.project_id
        pub = pinned(actor, pid, body.publication, "Publication")
        from .account_quota import ensure_intent_editable

        ensure_intent_editable(actor, pub, body.account.id)
        account = pinned(actor, pid, body.account, "Account")
        pkg = pinned(actor, pid, body.package, "DeliveryPackage")
        snap = verify_package(actor, pkg, check_archive=True)
        d = pub["data"]
        if (
            d.get("status") != "draft"
            or d.get("outcome") != "not_sent"
            or d.get("channel") != "manual"
        ):
            raise DomainError("MANUAL_DRAFT_REQUIRED", "请选择尚未发出的人工平台草稿意图。", 409)
        if (
            d.get("account_id") != account["id"]
            or (snap["account"] and snap["account"] != ref(account))
            or (d.get("artifact_id"), d.get("artifact_version"))
            != (snap["artifact"]["id"], snap["artifact"]["version"])
        ):
            raise DomainError(
                "PUBLICATION_SCOPE_MISMATCH", "交接包与发布意图的成片版本或目标账号不同。", 409
            )
        market = body.market
        if not snap["market"] or snap["market"].casefold() != market.casefold():
            raise DomainError(
                "PRODUCT_MARKET_MISMATCH", "实际挂商品前须重做已明确目标市场的交接包。", 409
            )
        if any(
            not p["shop_product_id"]
            or not p["market"]
            or p["market"].casefold() != market.casefold()
            or p["quantity"] <= 0
            for p in snap["products"]
        ):
            raise DomainError(
                "SHOP_FACTS_INCOMPLETE", "商品缺少平台商品号、同市场可售证据或正库存。", 409
            )
        if len({p["shop_product_id"] for p in snap["products"]}) != len(snap["products"]):
            raise DomainError(
                "DUPLICATE_SHOP_PRODUCT", "平台商品号无法唯一对应本次绑定，请核实规格映射。", 409
            )
        creator_source = source_evidence(actor, pid, body.creator_source)
        binding = {
            "package": ref(pkg),
            "account": ref(account),
            "market": market,
            "creator_source": ref(creator_source),
            "reviewed_by": actor.actor_id,
            "reviewed_at": now(),
            "eligibility_evidence": body.eligibility_evidence,
            "eligibility_expires_at": timestamp(body.eligibility_expires_at).isoformat(),
            "metadata_hash": digest(snap),
            "verification": "manual_creator_market_and_item_review",
            "attachment_status": "pending_platform_action",
        }
        artifact = pinned(actor, pid, snap["artifact"], "Artifact")
        artifact_reviews(actor, pid, artifact, account["id"])
        valid_creator(actor, pid, binding)
        metadata = {
            **d["metadata"],
            "commercial_handoff": snap["disclosure"],
            "requested_shop_product_ids": [p["shop_product_id"] for p in snap["products"]],
        }
        binding["approved_metadata_hash"] = digest(metadata)
        result = revise(
            actor,
            pub,
            {"commerce_binding": binding, "metadata": metadata, "metadata_hash": digest(metadata)},
            body.eligibility_evidence,
            [*pub["refs"], ref(pkg), ref(creator_source), ref(account)],
        )
        return {"publication": result}

    return ok(on_request("commercial-handoff-binding", body, actor, apply), request)


def observed_intent(actor, post, publication):
    """Resolve the commercial intent recorded with the original post receipt."""
    pid = publication["project_id"]
    original = record(actor, post["id"], "Post", version=1, project=pid)
    pub_ref = next(
        (
            r
            for r in original["refs"]
            if r["kind"] == "Publication" and r["id"] == publication["id"]
        ),
        None,
    )
    if not pub_ref:
        raise DomainError("POST_BINDING_MISMATCH", "原始帖子回执缺少发布意图版本。", 409)
    intent = record(actor, pub_ref["id"], "Publication", version=pub_ref["version"], project=pid)
    data = intent["data"]
    binding = data.get("commerce_binding")
    if not binding or binding["account"]["id"] != post["data"].get("account_id"):
        raise DomainError("POST_BINDING_MISMATCH", "原始回执未绑定该账号的商品意图。", 409)
    package = record(
        actor,
        binding["package"]["id"],
        "DeliveryPackage",
        version=binding["package"]["version"],
        project=pid,
    )
    snap = package["data"].get("commercial_metadata")
    if package["data"].get("purpose") != "publish_handoff" or not isinstance(snap, dict):
        raise DomainError("COMMERCIAL_BINDING_CHANGED", "原始包缺少商业交接快照。", 409)
    verify_bound_metadata(data, snap)
    return intent, package, snap


class Attachment(Command):
    post: ChosenVersion
    publication: ChosenVersion
    evidence_source: ChosenVersion
    observed_shop_product_ids: list[Label] = Field(max_length=30)
    observed_disclosure_text: str | None = Field(default=None, max_length=3000)
    observed_disclosure_kind: Literal[
        "own_brand", "paid_partnership", "own_brand_and_paid_partnership", "absent", "unknown"
    ]
    evidence: Note
    observed_at: str


@router.post("/observations")
def observe(body: Attachment, request: Request, actor: Actor = Depends(current_actor)):
    require_approval(actor)

    def apply():
        pid = body.project_id
        pub = pinned(actor, pid, body.publication, "Publication")
        post = pinned(actor, pid, body.post, "Post")
        if (
            not pub["data"].get("commerce_binding")
            or post["data"].get("publication_id") != pub["id"]
            or post["data"].get("account_id") != pub["data"]["account_id"]
            or not post["data"].get("platform_post_id")
        ):
            raise DomainError(
                "POST_BINDING_MISMATCH", "请选择该创作者账号及已绑定发布意图的实际帖子。", 409
            )
        intent, package, snap = observed_intent(actor, post, pub)
        observed = timestamp(body.observed_at)
        if observed > datetime.now(timezone.utc) or observed < timestamp(
            intent["data"]["commerce_binding"].get("reviewed_at", intent["created_at"])
        ):
            raise DomainError(
                "INVALID_OBSERVATION_TIME", "观测必须发生在挂商品意图核对后且不能在未来。"
            )
        source = source_evidence(actor, pid, body.evidence_source)
        old = post["data"].get("commercial_observation")
        if old and observed <= timestamp(old["observed_at"]):
            raise DomainError("STALE_OBSERVATION", "请使用晚于现有核对记录的实际观测时间。", 409)
        if len(set(body.observed_shop_product_ids)) != len(body.observed_shop_product_ids):
            raise DomainError("DUPLICATE_SHOP_PRODUCT", "观测清单中商品号重复。")
        matches = (
            set(body.observed_shop_product_ids) == {p["shop_product_id"] for p in snap["products"]}
            and body.observed_disclosure_kind == snap["disclosure"]["kind"]
            and (body.observed_disclosure_text or "").strip() == snap["disclosure"]["text"].strip()
        )
        observation = {
            "status": "matched_as_observed" if matches else "mismatch",
            "shop_product_ids": body.observed_shop_product_ids,
            "disclosure_kind": body.observed_disclosure_kind,
            "disclosure_text": body.observed_disclosure_text,
            "observed_at": observed.isoformat(),
            "platform_post_id": post["data"]["platform_post_id"],
            "source": ref(source),
            "evidence": body.evidence,
            "reviewed_by": actor.actor_id,
            "basis": "human_observation",
            "publication": ref(pub),
            "receipt_intent": ref(intent),
            "frozen_package": ref(package),
        }
        updated = revise(
            actor,
            post,
            {"commercial_observation": observation},
            body.evidence,
            [*post["refs"], ref(pub), ref(intent), ref(package), ref(source)],
        )
        return {"post": updated}

    return ok(on_request("commercial-handoff-observation", body, actor, apply), request)


class Collect(Command):
    package: ChosenVersion
    recipient: Label
    checksum: str = Field(pattern="^[a-f0-9]{64}$")
    evidence: Note
    collected_at: str


@router.post("/receipts")
def receive(body: Collect, request: Request, actor: Actor = Depends(current_actor)):
    require_write(actor)

    def apply():
        pkg = pinned(actor, body.project_id, body.package, "DeliveryPackage")
        d = pkg["data"]
        if (
            d.get("purpose") != "publish_handoff"
            or d.get("checksum") != body.checksum
            or d.get("handoff_state") != "ready"
        ):
            raise DomainError(
                "COMMERCIAL_RECEIPT_MISMATCH", "仅可确认待收讫且摘要吻合的商品交接包。", 409
            )
        at = timestamp(body.collected_at)
        if at > datetime.now(timezone.utc) or at < timestamp(pkg["created_at"]):
            raise DomainError("INVALID_RECEIPT_TIME", "收讫时间必须位于生成时间和当前时间之间。")
        proof = {
            "recipient": body.recipient,
            "checksum": body.checksum,
            "collected_at": at.isoformat(),
            "evidence": body.evidence,
            "attested_by": actor.actor_id,
        }
        return {
            "package": revise(
                actor,
                pkg,
                {"handoff_state": "receipt_recorded", "collection_evidence": proof},
                body.evidence,
            )
        }

    return ok(on_request("commercial-handoff-receipt", body, actor, apply), request)

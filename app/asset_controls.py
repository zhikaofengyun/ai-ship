"""Keep materialized lifecycle results distinct from manually editable record metadata."""

from .errors import DomainError

TIER_FIELDS = {
    "storage_tier",
    "tier_reason",
    "tier_revision",
    "tag_base_revision",
    "cold_location_id",
    "recovery_probe",
}
MATERIAL_FIELDS = {"asset_id", "blob_location_id", "content_hash", "source_id", "variant"}
OUTPUT_FIELDS = {
    ("Plan", "U05"): {
        "feature_id",
        "route_id",
        "status",
        "candidates",
        "constraints",
        "unfilled",
        "omissions",
        "cooldown_policies",
        "lifecycle_request_id",
    },
    ("Policy", "U04"): {
        "feature_id",
        "route_id",
        "status",
        "typed_config",
        "evaluation",
        "lifecycle_request_id",
    },
    ("AssetScore", "U04"): {
        "feature_id",
        "route_id",
        "asset_version_ref",
        "policy_version",
        "fatigue",
        "pressure",
        "tier",
        "tier_reason",
        "evaluated_at",
        "expires_at",
    },
}


def projection(kind, data):
    fields = OUTPUT_FIELDS.get((kind, data.get("feature_id")))
    if not fields:
        return None
    # A feature-labelled manual draft without any calculated output remains editable.
    return fields if fields.intersection(data) - {"feature_id", "route_id", "status"} else None


def manual_asset_data(kind, data, old=None):
    previous = old["data"] if old else {}
    protected = set()
    if kind in {"Asset", "AssetVersion"}:
        protected.update(TIER_FIELDS)
        if "storage_tier" in previous:
            protected.update(MATERIAL_FIELDS)
    old_projection = projection(kind, previous)
    if old_projection:
        protected.update(old_projection)
    elif projection(kind, data):
        raise DomainError("ASSET_OPERATION_REQUIRED", "计算结果须由对应素材操作生成。", 403)
    if not protected:
        return data
    result = dict(data)
    for key in protected:
        if key in data and (key not in previous or data[key] != previous[key]):
            raise DomainError(
                "ASSET_OPERATION_REQUIRED",
                "此分层或计算字段只能通过对应素材操作修改。",
                403,
                {"field": key},
            )
        if key in previous:
            result[key] = previous[key]
    return result

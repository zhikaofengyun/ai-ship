// A media file, its immutable derived versions, and an independent Audio record
// share one selection model. Original AssetVersion rows remain addressable for
// pinned timelines, but do not duplicate their parent in the media library.
export function mediaLibrary(records) {
  return records.filter(
    (r) =>
      r.data?.blob_location_id &&
      (r.kind === "Asset" ||
        r.kind === "Audio" ||
        (r.kind === "AssetVersion" && r.data.variant !== "original")),
  );
}

export function mediaType(record) {
  if (!record) return "unknown";
  if (record.kind === "Audio") return "audio";
  const type = record.data?.media_type || record.data?.probe?.media_type || "";
  const probe = record.data?.probe || {};
  if (type.startsWith("image") || probe.is_still) return "image";
  if (type.startsWith("video") || probe.has_video) return "video";
  if (type.startsWith("audio") || probe.has_audio) return "audio";
  return "unknown";
}

export function mediaName(record) {
  return (
    record?.data?.name || record?.data?.filename || record?.id || "来源不可用"
  );
}

export function mediaVariant(record) {
  if (record?.kind === "Audio")
    return record.data.variant === "derivative" ? "音频处理版本" : "独立音轨";
  if (record?.kind === "AssetVersion" && record.data.variant !== "original")
    return "衍生版本";
  return "原始素材";
}

export function mediaDuration(record) {
  const value = Number(
    record?.data?.probe?.duration ?? record?.data?.duration ?? 0,
  );
  return Number.isFinite(value) && value > 0 ? value : 0;
}

export function selectedMedia(clip, records) {
  return records.find((r) => r.id === (clip.asset_version_id || clip.asset_id));
}

export function mediaBinding(record, records) {
  if (record.kind === "Audio") return { asset_id: record.id };
  if (record.kind === "AssetVersion") {
    if (!record.data.asset_id)
      throw new Error("所选版本没有原始素材归属，先核对来源。");
    return { asset_id: record.data.asset_id, asset_version_id: record.id };
  }
  const original = records.find(
    (r) =>
      r.kind === "AssetVersion" &&
      r.data.asset_id === record.id &&
      r.data.variant === "original",
  );
  if (!original)
    throw new Error("原始素材版本暂不可用，重新读取工作区后再试。");
  return { asset_id: record.id, asset_version_id: original.id };
}

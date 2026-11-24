// Chunked browser intake. Only acknowledged offsets advance the progress indicator.
// Re-selecting the same file resumes its existing ImportEntry; an unknown result
// is reconciled with that entry instead of starting a new external effect.
async function sha(bytes) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
}
export async function uploadFile(
  api,
  projectId,
  file,
  {
    onProgress = () => {},
    shouldPause = () => false,
    shouldCancel = () => false,
  } = {},
) {
  const block = 4 * 1024 * 1024;
  const sample = new Uint8Array(
    await new Blob([
      file.slice(0, 65536),
      file.slice(Math.max(0, file.size - 65536)),
    ]).arrayBuffer(),
  );
  const fingerprint = await sha(
    new TextEncoder().encode(
      `${file.name}|${file.size}|${file.lastModified}|${await sha(sample)}`,
    ),
  );
  const key = `ship-upload:${projectId}:${fingerprint}`;
  let cached;
  try {
    cached = JSON.parse(localStorage.getItem(key) || "null");
  } catch {}
  let state;
  if (cached?.entry_id) {
    try {
      state = await api(`/media/uploads/${cached.entry_id}`);
      if (state.entry?.data?.status === "cancelled") {
        localStorage.removeItem(key);
        state = null;
      }
    } catch (error) {
      if (error.code === "NOT_FOUND" || error.code === "RECORD_NOT_FOUND") {
        localStorage.removeItem(key);
      } else throw error;
    }
  }
  if (!state) {
    state = await api("/media/uploads", {
      method: "POST",
      body: {
        project_id: projectId,
        filename: file.name,
        size_bytes: file.size,
        source_note: file.webkitRelativePath || "用户在浏览器选择的本地文件",
      },
    });
    localStorage.setItem(
      key,
      JSON.stringify({
        entry_id: state.entry.id,
        filename: file.name,
        size_bytes: file.size,
        fingerprint,
      }),
    );
  }
  const entryId = state.entry.id;
  let offset = Number(state.offset ?? state.entry.data.transferred_bytes ?? 0);
  if (!Number.isInteger(offset) || offset < 0 || offset > file.size)
    throw new Error("服务端续传偏移与本地文件不匹配，已停止上传。");
  while (offset < file.size) {
    if (shouldCancel()) {
      await api(`/media/uploads/${entryId}`, { method: "DELETE" });
      localStorage.removeItem(key);
      return { cancelled: true, entry_id: entryId };
    }
    if (shouldPause()) {
      onProgress({
        offset,
        total: file.size,
        entry_id: entryId,
        status: "paused",
      });
      return { paused: true, entry_id: entryId };
    }
    const bytes = await file
        .slice(offset, Math.min(offset + block, file.size))
        .arrayBuffer(),
      digest = await sha(bytes);
    const result = await api(`/media/uploads/${entryId}?offset=${offset}`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Chunk-Sha256": digest,
      },
      body: bytes,
    });
    const acknowledged = Number(
      result.offset ?? result.entry?.data?.transferred_bytes,
    );
    if (
      !Number.isInteger(acknowledged) ||
      acknowledged !== offset + bytes.byteLength
    )
      throw new Error(
        "服务端未确认预期分块长度，请重新选择同一文件核对后继续。",
      );
    offset = acknowledged;
    onProgress({
      offset,
      total: file.size,
      entry_id: entryId,
      status: "importing",
    });
  }
  if (shouldCancel()) {
    await api(`/media/uploads/${entryId}`, { method: "DELETE" });
    localStorage.removeItem(key);
    return { cancelled: true, entry_id: entryId };
  }
  onProgress({
    offset,
    total: file.size,
    entry_id: entryId,
    status: "verifying",
  });
  const result = await api(`/media/uploads/${entryId}/complete`, {
    method: "POST",
  });
  localStorage.removeItem(key);
  return result;
}

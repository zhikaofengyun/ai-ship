import { modal, download, esc, select, field, uid, toast } from "./ui.js";
import { mediaType } from "./media.js";

export async function openTimelineExchange(action, { ctx, save, onImported }) {
  const projectId = ctx.requireProject();
  const endpoint =
    "/projects/" + encodeURIComponent(projectId) + "/timeline-exchange/";
  const send = (path, body) =>
    ctx.api(endpoint + path, { method: "POST", body });
  if (action === "export-otio") {
    const timeline = await save();
    if (!timeline) throw new Error("请先保存时间线。");
    const r = await send("export", {
      timeline_id: timeline.id,
      expected_revision: timeline.version,
    });
    const d = modal(
      "导出剪辑工程",
      `<p>${esc(r.name)} · OpenTimelineIO</p><p>工程内含剪辑决定。媒体文件需单独交接并在外部编辑器重新链接。</p>${r.notes.map((n) => `<p>${esc(n)}</p>`).join("")}<div class="dialog-actions"><button class="button primary" data-download-otio>下载 .otio</button></div>`,
    );
    d.querySelector("[data-download-otio]").onclick = () => {
      download((r.name || "剪辑") + ".otio", r.content, "application/json");
      d.close();
    };
    return;
  }
  modal(
    "导入剪辑工程",
    `<form data-form>${field("OpenTimelineIO 工程", "file", "", "file", 'required accept=".otio,application/json"')}<div class="dialog-actions"><button type="submit" class="button primary">检查工程</button></div></form>`,
    async (f) => {
      const file = f.elements.file.files[0];
      if (file.size > 1_500_000) throw new Error("工程文件超过 1.5 MB。");
      const content = await file.text();
      const report = await send("inspect", { content });
      const request_id = uid();
      const media = ctx.state.records.filter(
        (r) =>
          r.data.blob_location_id && ["AssetVersion", "Audio"].includes(r.kind),
      );
      modal(
        "核对导入工程",
        `<form data-form><p>${esc(report.name)} · ${report.tracks} 条轨道 · ${report.clip_count} 个片段</p>${report.unresolved
          .map((m) => {
            const picks = media
              .filter((r) =>
                m.kind === "Audio"
                  ? mediaType(r) === "audio" || r.data.probe?.has_audio
                  : ["video", "image"].includes(mediaType(r)),
              )
              .map((r) => {
                const parent = ctx.state.records.find(
                  (p) => p.id === r.data.asset_id,
                );
                return [
                  r.id,
                  r.data.filename ||
                    r.data.name ||
                    parent?.data.filename ||
                    "项目素材",
                ];
              });
            return select(
              m.name,
              m.slot,
              [["", "选择当前项目的素材"], ...picks],
              "",
              "required",
            );
          })
          .join(
            "",
          )}${report.notes.map((n) => `<p>${esc(n)}</p>`).join("")}${report.notes.length ? '<label><input type="checkbox" required name="ack"> 接受上述元数据差异</label>' : ""}<div class="dialog-actions"><button type="submit" class="button primary">创建导入草稿</button></div></form>`,
        async (confirmed) => {
          const fields = new FormData(confirmed),
            bindings = Object.fromEntries(
              report.unresolved.map((m) => [m.slot, fields.get(m.slot)]),
            );
          const result = await send("import", {
            content,
            bindings,
            request_id,
            acknowledge_metadata: fields.has("ack"),
          });
          await onImported(result);
          toast("已打开导入的剪辑草稿");
        },
      );
    },
  );
}

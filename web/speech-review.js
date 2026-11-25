import { area, download, esc, field, modal, select, toast, uid } from "./ui.js";

const protocol = "vosk-source-captions-v1";
const pin = ({ id, version }) => ({ id, version });
const post = (ctx, path, body) =>
  ctx.api("/production/speech/" + path, { method: "POST", body });
function name(ctx, ref) {
  const item = ctx.state.records.find((r) => r.id === ref.id);
  const parent = ctx.state.records.find((r) => r.id === item?.data?.asset_id);
  return (
    item?.data?.filename || parent?.data?.filename || item?.data?.name || ref.id
  );
}
function approved(ctx, pid, receipt) {
  const dialog = modal(
    "已审核的源音字幕",
    '<p>字幕使用源文件时间，可在时间轴的「导入字幕文件」中载入。</p><button class="button primary" type="button" data-download>下载 SRT 字幕</button>',
  );
  dialog.querySelector("[data-download]").onclick = async () => {
    try {
      const result = await ctx.api(
        "/production/speech/reviews/" +
          encodeURIComponent(receipt.id) +
          "/captions?project_id=" +
          encodeURIComponent(pid),
      );
      download(result.filename, result.content, result.mime_type);
    } catch (e) {
      toast(e.message, "error");
    }
  };
}
function review(ctx, pid, item) {
  const { cues, source, model_id, model_sha256, source_window } = item.data;
  if (!cues.length) {
    modal(
      "未检出语音",
      `<p>${esc(name(ctx, source))} · ${source_window.map((n) => n.toFixed(2)).join("–")} 秒</p><p>识别完成，此区间没有词级结果。</p>`,
    );
    return;
  }
  const dialog = modal(
    "逐句审核源音字幕",
    `<p>${esc(name(ctx, source))} · ${esc(model_id)} · ${esc(model_sha256.slice(0, 12))}</p><audio controls preload="metadata" src="/api/v1/media/${encodeURIComponent(source.id)}/file"></audio><form data-form class="form-grid">${cues.map((c, i) => `<fieldset><legend>第 ${i + 1} 句</legend><label><input type="checkbox" name="keep-${i}" checked>采纳</label> <button type="button" class="button secondary" data-seek="${c.start}" data-end="${c.end}">试听 ${c.start.toFixed(2)}–${c.end.toFixed(2)} 秒</button>${area("审核文本", "cue-" + i, c.text, "maxlength=2000")}</fieldset>`).join("")}${field("审核依据", "reason", "", "text", "required maxlength=2000")}<div class="dialog-actions"><button type="submit" class="button primary">保存审核</button></div></form>`,
    async (f) => {
      const selected = cues.flatMap((_, i) =>
        f.elements["keep-" + i].checked
          ? [{ index: i, text: f.elements["cue-" + i].value.trim() }]
          : [],
      );
      if (!selected.length) throw new Error("请选择至少一句字幕。");
      const { approval } = await post(ctx, "review", {
        project_id: pid,
        request_id: uid(),
        candidate: pin(item),
        reason: f.elements.reason.value,
        cues: selected,
      });
      await ctx.reloadData();
      approved(ctx, pid, approval);
    },
    true,
  );
  const player = dialog.querySelector("audio");
  dialog.addEventListener("close", () => player.pause(), { once: true });
  let stopAt = null;
  player.addEventListener("timeupdate", () => {
    if (stopAt !== null && player.currentTime >= stopAt) {
      player.pause();
      stopAt = null;
    }
  });
  for (const button of dialog.querySelectorAll("[data-seek]"))
    button.onclick = async () => {
      stopAt = Number(button.dataset.end);
      player.currentTime = Number(button.dataset.seek);
      try {
        await player.play();
      } catch (e) {
        toast(e.message, "error");
      }
    };
}
export async function openSpeech(ctx, manual) {
  const pid = ctx.requireProject();
  const catalog = await ctx.api("/production/speech/models");
  const models = catalog.models.filter((m) => m.available);
  const sources = [
    ...ctx.records("AssetVersion"),
    ...ctx.records("Audio"),
  ].filter((r) => r.data.probe?.has_audio !== false);
  const candidates = ctx
    .records("Audit")
    .filter((r) => r.data.protocol === protocol);
  const approvals = ctx
    .records("Approval")
    .filter((r) => r.data.protocol === protocol);
  const dialog = modal(
    "识别对白与逐句审核",
    `<form data-form class="form-grid">${select(
      "源素材或音轨",
      "source",
      sources.map((r) => [r.id, name(ctx, r)]),
      "",
      "required",
    )}${select(
      "本地语音模型",
      "model",
      models.map((m) => [m.id, m.id + " · " + m.language]),
      "",
      "required",
    )}${!models.length ? "<p>本机尚未配置可用的识别模型。</p>" : ""}${field("开始（源文件秒）", "start", "0", "number", 'min="0" step="0.001" required')}${field("结束（留空到片尾，每次最多十分钟）", "end", "", "number", 'min="0.001" step="0.001"')}<div class="dialog-actions"><button type="submit" class="button primary" ${models.length && sources.length ? "" : "disabled"}>识别此区间</button></div></form>${manual ? '<button type="button" class="button secondary" data-manual>人工标记词句时码</button>' : ""}${candidates.length ? "<h3>已识别的区间</h3>" : ""}${candidates.map((r) => `<button type="button" class="button secondary" data-candidate="${esc(r.id)}">${esc(name(ctx, r.data.source))} · ${r.data.cues.length} 句 · ${r.data.source_window.map((n) => n.toFixed(1)).join("–")} 秒</button>`).join("")}${approvals.length ? "<h3>已审核的字幕</h3>" : ""}${approvals.map((r) => `<button class="button secondary" type="button" data-approved="${esc(r.id)}">${esc(name(ctx, r.data.source))} · ${r.data.captions.length} 句</button>`).join("")}`,
    async (f) => {
      const key = f.elements.source.value;
      const body = {
        project_id: pid,
        request_id: uid(),
        source: pin(sources.find((r) => r.id === key)),
        model_id: f.elements.model.value,
        start_seconds: Number(f.elements.start.value),
      };
      if (f.elements.end.value) body.end_seconds = Number(f.elements.end.value);
      const { candidate } = await post(ctx, "recognize", body);
      await ctx.reloadData();
      review(ctx, pid, candidate);
    },
  );
  if (manual) dialog.querySelector("[data-manual]").onclick = manual;
  for (const el of dialog.querySelectorAll("[data-candidate]"))
    el.onclick = () =>
      review(
        ctx,
        pid,
        candidates.find((r) => r.id === el.dataset.candidate),
      );
  for (const el of dialog.querySelectorAll("[data-approved]"))
    el.onclick = () =>
      approved(
        ctx,
        pid,
        approvals.find((r) => r.id === el.dataset.approved),
      );
}

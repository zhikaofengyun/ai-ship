import { area, esc, modal, select, uid } from "./ui.js";

const status = {
  failed: "失败",
  running: "运行中",
  superseded: "已移交",
  succeeded: "已生成成片",
};

export async function openRenderRecovery(ctx, selected = "") {
  const project = ctx.requireProject();
  const records = await ctx.api(
    "/records?kind=RenderJob&limit=500&project_id=" +
      encodeURIComponent(project),
  );
  const localJob = (r) =>
    r.kind === "RenderJob" &&
    r.project_id === project &&
    r.data.renderer === "ffmpeg_local" &&
    !r.data.execution_context &&
    !r.data.origin_execution_context;
  const jobs = (records.items || records).filter(localJob);
  if (selected && !jobs.some((j) => j.id === selected)) {
    const related = await ctx.api("/records/" + encodeURIComponent(selected));
    if (!localJob(related))
      throw new Error("关联尝试不属于当前项目的独立本地渲染。");
    jobs.unshift(related);
  }
  if (!jobs.length) {
    modal(
      "渲染恢复",
      "<p>本项目尚无独立的本地渲染任务。可先在剪辑室锁定时间线并渲染成片。</p>",
    );
    return;
  }
  const requestIds = new Map();
  const d = modal(
    "渲染恢复",
    '<form data-form class="form-stack">' +
      select(
        "渲染尝试",
        "attempt",
        jobs.map((j) => [
          j.id,
          (status[j.data.status] || j.data.status) + " · " + j.id,
        ]),
        selected ||
          jobs.find(
            (j) =>
              !j.data.continuation &&
              ["failed", "running"].includes(j.data.status),
          )?.id,
      ) +
      "<div data-details></div><div data-retry>" +
      area("恢复原因", "reason", "", 'maxlength="2000"') +
      '<label data-takeover><input type="checkbox" name="interrupt"> 已核对运行状态，确认接管仍在途的尝试</label>' +
      "<p>将从锁定时间线的起点重新渲染全片。成功后可播放并查看完整解码结果。</p>" +
      '<button class="button primary" type="submit">重新渲染全片</button></div></form>',
    async (form) => {
      const j = jobs.find((r) => r.id === form.elements.attempt.value);
      const reason = form.elements.reason.value.trim();
      if (!reason) throw new Error("请填写恢复原因。");
      const interrupt =
        j.data.status === "running" && form.elements.interrupt.checked;
      if (j.data.status === "running" && !interrupt)
        throw new Error("请确认接管在途尝试。");
      const key = JSON.stringify([j.id, reason, interrupt]);
      if (!requestIds.has(key)) requestIds.set(key, uid());
      const result = await ctx.api(
        "/media/render-jobs/" + encodeURIComponent(j.id) + "/recover",
        {
          method: "POST",
          body: {
            project_id: project,
            request_id: requestIds.get(key),
            expected_revision: j.version,
            reason,
            interrupt_running: interrupt,
          },
        },
      );
      await openRenderRecovery(ctx, result.job.id);
    },
  );
  const sync = () => {
    const j = jobs.find(
      (r) => r.id === d.querySelector("[name=attempt]").value,
    );
    const data = j.data,
      snap = data.checkpoint;
    const checkpoint = snap
      ? `${snap.profile.width} × ${snap.profile.height} · ${snap.profile.fps.num}/${snap.profile.fps.den} fps · ${snap.media.length} 个片段`
      : "旧任务将在恢复时核验原始引用和规格";
    const detail = d.querySelector("[data-details]");
    const next = data.continuation?.job_id;
    detail.innerHTML =
      `<p>锁定时间线 ${esc(data.timeline_id)} · v${esc(data.timeline_version)}</p><p>${esc(checkpoint)}</p>` +
      (data.error_code ? `<p>失败记录 · ${esc(data.error_code)}</p>` : "") +
      (data.qc?.baseline_comparison
        ? "<p>与原成片逐流核对，完整画面和音频解码哈希一致。</p>"
        : "") +
      (data.previous_attempt
        ? `<p>前次尝试 · ${esc(data.previous_attempt.id)}</p>`
        : "") +
      (next
        ? '<button class="button secondary" type="button" data-successor>查看后续尝试</button>'
        : "") +
      (data.status === "succeeded"
        ? `<video controls preload="metadata" src="/api/v1/media/artifacts/${encodeURIComponent(data.artifact_id)}/file"></video><p>${data.qc?.checks?.full_decode ? "全片解码通过" : "请核对成片技术清单"} · 内容审阅另行确认</p>`
        : "") +
      '<button class="button secondary" type="button" data-reload>刷新状态</button>';
    if (next)
      detail.querySelector("[data-successor]").onclick = () =>
        openRenderRecovery(ctx, next).catch((e) =>
          ctx.toast(e.message, "error"),
        );
    detail.querySelector("[data-reload]").onclick = () =>
      openRenderRecovery(ctx, j.id).catch((e) => ctx.toast(e.message, "error"));
    d.querySelector("[data-retry]").hidden =
      !!next || !["failed", "running", "succeeded"].includes(data.status);
    d.querySelector("[data-takeover]").hidden = data.status !== "running";
    d.querySelector("[name=interrupt]").checked = false;
  };
  d.querySelector("[name=attempt]").onchange = sync;
  sync();
}

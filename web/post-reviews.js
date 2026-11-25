import { placementPerformance } from "./placement-performance.js";
import { area, esc, field, formData, modal, select, uid } from "./ui.js";

export function postReview(ctx) {
  const project_id = ctx.requireProject(),
    path = "/production/post-reviews/";
  const dialog = modal(
    "帖子表现复盘",
    '<div class="dialog-actions"><button data-tiktok class="button secondary">同步 TikTok 指标</button><button data-csv class="button secondary">导入指标 CSV</button><button data-retention class="button secondary">按镜头核对留存</button><button data-placements class="button secondary">比较素材位置表现</button><button data-compare class="button primary">比较两个帖子</button></div>',
  );
  dialog.querySelector("[data-placements]").onclick = () =>
    placementPerformance(ctx, () => postReview(ctx));
  dialog.querySelector("[data-tiktok]").onclick = () => syncTikTok(ctx);
  dialog.querySelector("[data-retention]").onclick = () =>
    retention(ctx, project_id, path);
  dialog.querySelector("[data-csv]").onclick = () => {
    const request_id = uid();
    modal(
      "导入指标 CSV",
      '<form data-form class="form-stack">' +
        field(
          "UTF-8 CSV",
          "file",
          "",
          "file",
          'accept=".csv,text/csv" required',
        ) +
        '<p>列名沿用指标接口。帖子比较需要 account_id、post_record_id、metric_name、value、unit、window_start、window_end、observed_at、source、counting_mode、paid_or_organic、data_mature_after。缺值需填写 missing_reason。</p><button type="submit" class="button primary">导入并核对</button></form>',
      async (f) => {
        const file = f.elements.file.files[0];
        if (file.size > 2000000) throw new Error("单个 CSV 上限为 2 MB。");
        const csv_text = new TextDecoder("utf-8", {
          fatal: true,
          ignoreBOM: true,
        }).decode(await file.arrayBuffer());
        const r = await ctx.api(path + "archive", {
          method: "POST",
          body: { request_id, project_id, filename: file.name, csv_text },
        });
        await ctx.reloadData();
        await ctx.refresh();
        modal(
          "CSV 导入结果",
          `<p>新增 ${r.count} 条指标快照，${r.duplicates.length} 行与已有快照相同。</p>`,
        );
      },
    );
  };
  dialog.querySelector("[data-compare]").onclick = () => {
    const metrics = ctx.records("Metric").filter((r) => r.data.post_record_id);
    const options = [
      ["", "选择一个带帖子绑定的指标"],
      ...metrics.map((r) => [
        r.id,
        [
          ctx.records("Post").find((p) => p.id === r.data.post_record_id)?.data
            .platform_post_id || r.data.post_record_id,
          r.data.metric_name,
          r.data.value_or_null ?? "缺失",
          r.data.observed_at,
        ].join(" · "),
      ]),
    ];
    const request_id = uid();
    modal(
      "比较帖子并登记假设",
      '<form data-form class="form-stack">' +
        select("参照帖子与指标", "baseline", options, "", "required") +
        select("比较帖子与指标", "comparison", options, "", "required") +
        area("可能的解释", "hypothesis", "", "required") +
        area("反证或可能的混杂因素", "counterevidence", "", "required") +
        area("下一版改动", "proposed_change", "", "required") +
        area("验证方法", "experiment_method", "", "required") +
        field("停止条件", "stop_rule", "", "text", "required") +
        '<button type="submit" class="button primary">核对可比性并保存</button></form>',
      async (f) => {
        const body = formData(f);
        for (const k of ["baseline", "comparison"]) {
          const m = metrics.find((r) => r.id === body[k]);
          body[k] = { id: m.id, version: m.version };
        }
        const r = await ctx.api(path + "compare", {
          method: "POST",
          body: { project_id, request_id, ...body },
        });
        await ctx.reloadData();
        await ctx.refresh();
        const d = r.insight.data;
        modal(
          "整帖比较结果",
          `<p>后者减去参照值为 ${esc(d.difference)}。两组分别核对 ${d.observations.map((o) => o.snapshot_count).join("、")} 份同口径快照。</p><p>${esc(d.uncertainty)}</p>`,
        );
      },
    );
  };
}

function retention(ctx, project_id, path) {
  const posts = ctx
    .records("Post")
    .filter((r) => r.data.final_media_status === "verified_same");
  const timestamp = (label, key) =>
    field(label, key, "", "datetime-local", 'step="1" required');
  const request_id = uid();
  modal(
    "按镜头核对首播留存",
    `<form data-form class="form-stack">
    ${select("已核对最终成片的帖子", "post", [["", "选择帖子"], ...posts.map((p) => [p.id, p.data.title || p.data.platform_post_id || p.id])], "", "required")}
    ${posts.length ? "" : "<p>先在发布模块核对平台下载件与所交成片，再导入留存。</p>"}
    ${field("留存数据来源", "source", "", "text", 'placeholder="平台报表、文件或统计口径" required')}
    <div class="form-grid">${timestamp("统计窗口起点", "window_start")}${timestamp("统计窗口终点", "window_end")}${timestamp("数据观察时间", "observed_at")}
    ${select("流量口径", "traffic", [
      ["organic", "自然"],
      ["paid", "付费"],
      ["mixed", "混合"],
      ["unknown", "未知"],
    ])}</div>
    ${field("同一首播队列总人数", "denominator", "", "number", 'min="1" step="1" required')}
    ${area("采样点（每行两列：成片秒数、仍在观看人数）", "points", "", 'placeholder="粘贴平台导出的两列数字，可用逗号或制表符分隔" required')}
    <button type="submit" class="button primary">对应成片与源片段</button></form>`,
    async (f) => {
      const v = formData(f),
        post = posts.find((p) => p.id === v.post);
      const points = v.points
        .trim()
        .split(/\r?\n/)
        .map((line, i) => {
          const fields = line.trim().split(/[,，\t ]+/);
          if (
            fields.length !== 2 ||
            fields.some((c) => !c || !Number.isFinite(Number(c)))
          )
            throw new Error("第 " + (i + 1) + " 行需要秒数和人数两列数字。");
          return { at_seconds: Number(fields[0]), retained: Number(fields[1]) };
        });
      const result = await ctx.api(path + "retention-mapping", {
        method: "POST",
        body: {
          ...v,
          points,
          project_id,
          request_id,
          post: { id: post.id, version: post.version },
          denominator: Number(v.denominator),
          window_start: new Date(v.window_start).toISOString(),
          window_end: new Date(v.window_end).toISOString(),
          observed_at: new Date(v.observed_at).toISOString(),
        },
      });
      await ctx.reloadData();
      const d = result.insight.data;
      modal(
        "留存与源素材区间",
        `<p>${esc(d.uncertainty)}</p><div class="table-scroll"><table class="data-table">
        <thead><tr><th>成片区间（秒）</th><th>人数变化</th><th>百分点</th><th>同段素材与源区间</th></tr></thead>
        <tbody>${d.intervals
          .map(
            (
              i,
            ) => `<tr><td>${esc(i.start_seconds)} – ${esc(i.end_seconds)}</td><td>${esc(i.start_retained)} → ${esc(i.end_retained)}</td>
        <td>${Number(i.change_percentage_points).toFixed(2)}</td><td>${
          i.contributors
            .map((c) => {
              const r = ctx.state.records.find((r) => r.id === c.source_ref.id);
              return (
                esc(r?.data.name || r?.data.filename || c.source_ref.id) +
                " · " +
                c.source_interval_seconds
                  .map((n) => Number(n).toFixed(2))
                  .join("–") +
                "s"
              );
            })
            .join("<br>") || "区间无素材"
        }</td></tr>`,
          )
          .join("")}</tbody></table></div>`,
        null,
        true,
      );
    },
  );
}

export function syncTikTok(ctx) {
  const project_id = ctx.requireProject();
  const accounts = ctx
    .records("Account")
    .filter((a) => String(a.data.provider).toLowerCase() === "tiktok");
  const dlg = modal(
    "同步 TikTok 官方指标",
    `<form data-form class="form-stack">
    ${select("已绑定账号", "account", [["", "选择 TikTok 账号"], ...accounts.map((a) => [a.id, a.data.name || a.id])], "", "required")}
    <div data-candidates></div><button type="submit" class="button primary">同步选中帖子</button></form>`,
    async (f) => {
      const ids = new FormData(f).getAll("posts");
      if (!ids.length || ids.length > 20)
        throw new Error("每次选择同一账号的 1 至 20 条帖子。");
      const key = JSON.stringify(ids);
      if (key !== prior) {
        prior = key;
        request = uid();
      }
      const r = await ctx.api("/production/tiktok/metrics", {
        method: "POST",
        body: { project_id, post_ids: ids, request_id: request },
      });
      await ctx.reloadData();
      modal(
        "同步结果",
        `<p>已登记 ${esc(r.count)} 条指标，${esc(r.missing_posts.length)} 条帖子尚无返回。</p>${r.missing_posts.map((id) => `<p>${esc(id)}</p>`).join("")}`,
      );
    },
  );
  let request = "",
    prior = "",
    sequence = 0;
  const id = dlg.dataset.modalId,
    active = () =>
      dlg.open &&
      id === dlg.dataset.modalId &&
      ctx.state.projectId === project_id;
  const form = dlg.querySelector("[data-form]");
  form.elements.account.onchange = async () => {
    const ticket = ++sequence,
      target = dlg.querySelector("[data-candidates]");
    if (!form.elements.account.value) {
      target.replaceChildren();
      return;
    }
    target.textContent = "正在读取账号帖子…";
    const account = form.elements.account.value;
    try {
      const data = await ctx.api(
        "/records?" +
          new URLSearchParams({
            project_id,
            kind: "Post",
            q: account,
            limit: 2000,
          }),
      );
      if (!active() || sequence !== ticket) return;
      const posts = (data.items || data).filter(
        (p) => p.data.account_id === account && p.data.platform_post_id,
      );
      target.innerHTML = posts.length
        ? `<div class="batch-pins">${posts.map((p) => `<label><input type="checkbox" name="posts" value="${esc(p.id)}"><span>${esc(p.data.title || p.data.platform_post_id)}</span></label>`).join("")}</div>`
        : "这个账号还没有关联平台视频 ID 的帖子。";
    } catch (err) {
      if (active() && sequence === ticket) target.textContent = err.message;
    }
  };
}

export async function tiktokAccountConnection(ctx, id) {
  const project_id = ctx.requireProject();
  const route =
    "/production/tiktok/accounts/" + encodeURIComponent(id) + "/connection";
  const dlg = modal(
    "TikTok 只读连接",
    '<p role="status" data-info></p><p data-scopes></p><div class="dialog-actions"><button type="button" class="button secondary" data-action="suspend">暂停读取</button><button type="button" class="button primary" data-action="verify">核验账号与读取权限</button></div>',
  );
  const modalId = dlg.dataset.modalId,
    active = () =>
      dlg.open &&
      dlg.dataset.modalId === modalId &&
      ctx.state.projectId === project_id;
  const status = dlg.querySelector("[data-info]"),
    buttons = [...dlg.querySelectorAll("[data-action]")];
  let current;
  function busy(v) {
    buttons.forEach((b) => (b.disabled = v));
  }
  function show(r) {
    current = r;
    const names = {
      verified: "已核验",
      verified_read: "只读已核验",
      verified_read_only: "只读已核验",
      not_verified: "尚未核验",
      suspended: "已暂停",
      unverified: "待核验",
      unconfigured: "尚未绑定",
      unconnected: "未连接",
    };
    status.textContent = (names[r.state] || r.state) + " · v" + r.revision;
    const caps = r.capabilities || {};
    const features = Array.isArray(caps)
      ? caps
      : Object.entries(caps)
          .filter(([, v]) => v === true)
          .map(([k]) => k);
    dlg.querySelector("[data-scopes]").textContent = features.length
      ? "可用权限 · " + features.join("、")
      : "";
    busy(!["owner", "reviewer"].includes(ctx.state.health.actor.role));
  }
  async function read() {
    busy(true);
    try {
      const r = await ctx.api(
        route + "?" + new URLSearchParams({ project_id }),
      );
      if (active()) show(r);
    } catch (err) {
      if (active()) status.textContent = err.message;
    }
  }
  buttons.forEach(
    (b) =>
      (b.onclick = async () => {
        if (!current) return;
        busy(true);
        try {
          const r = await ctx.api(route, {
            method: "POST",
            body: {
              project_id,
              expected_revision: current.revision,
              action: b.dataset.action,
            },
          });
          if (active()) show(r);
        } catch (err) {
          if (active()) {
            await read();
            if (active()) status.textContent = err.message;
          }
        }
      }),
  );
  await read();
}

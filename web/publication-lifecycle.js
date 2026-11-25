import { $, area, esc, field, formData, modal, select, uid } from "./ui.js";

export function managePublication(ctx, p) {
  const pid = ctx.requireProject();
  if (!p) {
    const pubs = ctx.records("Publication");
    const d = modal(
      "选择发布意图",
      pubs.length
        ? pubs
            .map(
              (r, i) =>
                `<button type="button" class="list-row" data-intent="${i}">${esc(r.data.title || r.data.metadata?.title || r.id)}</button>`,
            )
            .join("")
        : "<p>当前项目没有发布意图。</p>",
    );
    d.querySelectorAll("[data-intent]").forEach(
      (el) =>
        (el.onclick = () =>
          managePublication(ctx, pubs[Number(el.dataset.intent)])),
    );
    return;
  }
  const title = p.data.title || p.data.metadata?.title || p.id;
  const closed = ["superseded", "cancelled_before_handoff"].includes(
    p.data.lifecycle_state,
  );
  const role = ctx.state.health.actor.role,
    write = ["owner", "editor", "worker"].includes(role),
    review = ["owner", "reviewer"].includes(role);
  const hasPost = p.data.post_refs?.length > 0;
  const routes = [
    ...(write && !closed && !hasPost
      ? [
          ["change", "变更意图"],
          ["cancel", "取消未交接意图"],
        ]
      : []),
    ...(review ? [["observe", "登记平台观察"]] : []),
  ];
  if (!routes.length) {
    modal(title, "<p>当前状态或身份没有可执行的变更。</p>");
    return;
  }
  const route =
    "/production/publication-lifecycle/" + encodeURIComponent(p.id) + "/";
  const recOptions = (kind) => [
    ["", "沿用当前版本"],
    ...ctx
      .records(kind)
      .map((r) => [
        r.id,
        (r.data.name || r.data.filename || r.id) + " · v" + r.version,
      ]),
  ];
  const pin = (kind, id) => {
    const r = ctx.records(kind).find((x) => x.id === id);
    return { id: r.id, version: r.version };
  };
  let lastKey = "",
    lastIntent = "";
  const d = modal(
    title,
    `<form data-form class="form-stack">${select("操作", "action", routes)}
    <div data-fields class="form-stack"></div>${area("操作依据", "reason", "", "maxlength=1000 required")}
    <button type="submit" class="button primary">保存变更</button></form>`,
    async (form) => {
      const v = formData(form),
        body = { expected_revision: p.version, reason: v.reason };
      if (v.action === "change") {
        if (v.new_title.trim()) body.title = v.new_title.trim();
        if (v.account) body.account = pin("Account", v.account);
        if (v.artifact) body.artifact = pin("Artifact", v.artifact);
        if (v.schedule === "clear") body.clear_schedule = true;
        if (v.schedule === "set") {
          body.local_datetime = v.local_datetime;
          body.timezone = v.timezone;
          if (v.fold !== "") body.fold = Number(v.fold);
        }
      } else if (v.action === "observe") {
        body.observation = v.observation;
        body.evidence = v.evidence;
        body.observed_at = new Date(v.observed_at).toISOString();
        if (v.observation === "post_removed") body.post = pin("Post", v.post);
      }
      const intent = JSON.stringify([v.action, body]);
      if (intent !== lastIntent) {
        lastIntent = intent;
        lastKey = uid();
      }
      const r = await ctx.api(route + v.action, {
        method: "POST",
        body: { ...body, request_id: lastKey },
      });
      await ctx.reloadData();
      if (ctx.state.projectId !== pid) return;
      await ctx.refresh();
      modal(
        "发布意图记录",
        `<p>${v.action === "change" ? "已建立后续意图 · " + esc(r.successor.data.title || r.successor.id) : v.action === "cancel" ? "已取消本地未交接意图。" : "已记录平台观察。"} </p>`,
      );
    },
  );
  const form = $("form[data-form]", d),
    box = $("[data-fields]", form);
  function fields() {
    const act = form.elements.action.value;
    if (act === "cancel") {
      box.innerHTML = "<p>此操作将关闭当前本地意图。</p>";
      return;
    }
    if (act === "change") {
      box.innerHTML =
        field("新标题（可选）", "new_title", "", "text", 'maxlength="2200"') +
        select("替换成片", "artifact", recOptions("Artifact")) +
        select("替换账号", "account", recOptions("Account")) +
        select("排程", "schedule", [
          ["keep", "保留"],
          ["set", "调整"],
          ["clear", "清除"],
        ]) +
        '<div data-schedule class="form-stack"></div>';
      form.elements.schedule.onchange = () => {
        const b = $("[data-schedule]", form);
        b.innerHTML =
          form.elements.schedule.value === "set"
            ? field(
                "新的当地时间",
                "local_datetime",
                "",
                "datetime-local",
                "required",
              ) +
              field(
                "IANA 时区",
                "timezone",
                p.data.timezone ||
                  Intl.DateTimeFormat().resolvedOptions().timeZone,
                "text",
                "required",
              ) +
              select("夏令时重叠时刻", "fold", [
                ["", "无歧义"],
                ["0", "第一次"],
                ["1", "第二次"],
              ])
            : "";
      };
      return;
    }
    const posts = ctx
      .records("Post")
      .filter((r) => r.data.publication_id === p.id);
    const options = hasPost
      ? [["post_removed", "已观察到帖子撤下"]]
      : [
          ["outcome_unknown", "交接结果未知"],
          ["confirmed_not_sent", "已核对未发送"],
        ];
    box.innerHTML =
      select("平台实际观察", "observation", options) +
      (hasPost
        ? select(
            "对应帖子",
            "post",
            [
              ["", "选择帖子"],
              ...posts.map((r) => [r.id, r.data.platform_post_id || r.id]),
            ],
            "",
            "required",
          )
        : "") +
      field(
        "观察时间（本地）",
        "observed_at",
        "",
        "datetime-local",
        'step="1" required',
      ) +
      area("后台回执或观察证据", "evidence", "", "maxlength=4000 required");
  }
  form.elements.action.onchange = fields;
  fields();
}

import { area, esc, field, formData, modal, select, uid } from "./ui.js";

const phases = {
  none: "未预留",
  held: "已预留",
  queued: "待分配",
  in_handoff: "已交接，等待核对",
  settled: "已与回执对账",
  stale: "意图版本已变",
  released: "已释放",
  expired: "预留已到期",
};
const reasons = {
  concurrency: "并发已满",
  daily_slots: "近 24 小时份额已满",
  earlier_request: "等待较早的任务",
  scheduled: "尚未到本地排期",
  RATE_LIMIT_BACKOFF: "等待限流退避",
  CAPABILITY_REFRESH_REQUIRED: "退避结束，须核对平台状态",
  QUOTA_CAPACITY_CHANGED: "名额发生变化",
  RECONCILE_FIRST: "请核对外部结果",
  ACCOUNT_UNAVAILABLE: "账号已失效",
  ACCOUNT_CHANGED: "账号绑定已变",
};
const apiBase = "/production/account-quota";
const button = (action, label) =>
  `<button type="button" class="button secondary" data-slot="${action}">${esc(label)}</button>`;
const getName = (r) => r.data.name || r.data.title || r.id;
const pin = (r) => ({ id: r.id, version: r.version });

export async function accountQuota(ctx, initialPub = null) {
  const project = ctx.requireProject();
  const accounts = ctx.records("Account"),
    pubs = ctx
      .records("Publication")
      .filter((p) => p.data.channel === "manual");
  let accountId = initialPub?.data.account_id || accounts[0]?.id,
    pubId = initialPub?.id;
  if (!accountId) {
    modal("账号配额", "<p>请先在交付与发布中添加账号。</p>");
    return;
  }
  async function draw() {
    const currentPubs = pubs.filter((p) => p.data.account_id === accountId);
    const pub = currentPubs.find((p) => p.id === pubId) || currentPubs[0];
    pubId = pub?.id;
    const state = await ctx.api(
      apiBase +
        "/" +
        encodeURIComponent(project) +
        "/" +
        encodeURIComponent(accountId) +
        (pub ? "?publication_id=" + encodeURIComponent(pub.id) : ""),
    );
    const policy = state.policy,
      ticket = state.ticket,
      stage = state.effective_state;
    const meta = policy?.data;
    const role = ctx.state.health?.actor?.role;
    const approver = ["owner", "reviewer"].includes(role),
      writer = ["owner", "editor", "worker"].includes(role);
    const d = modal(
      "账号配额与人工交接",
      select(
        "发布账号",
        "quota-account",
        accounts.map((r) => [r.id, getName(r)]),
        accountId,
      ) +
        (pub
          ? select(
              "人工发布意图",
              "quota-pub",
              currentPubs.map((p) => [p.id, getName(p)]),
              pub.id,
            )
          : "<p>此账号暂无人工发布意图。</p>") +
        (policy
          ? `<p>近 24 小时已回填 ${state.receipt_count_24h} / ${meta.daily_slots} 个帖子，并发占用 ${state.held_or_unknown} / ${meta.concurrent_slots}，其中 ${state.unresolved_handoffs} 项已交接待核对。</p>`
          : "<p>此账号尚未配置本地配额。</p>") +
        (pub
          ? `<p role="status">${esc(phases[stage] || stage)}${state.queue_position ? " · 可分配队列第 " + state.queue_position + " 位" : ""}${ticket?.data.wait_reason && stage === "queued" ? " · " + esc(reasons[ticket.data.wait_reason] || ticket.data.wait_reason) : ""}${stage === "held" ? " · 到期 " + esc(new Date(ticket.data.valid_until).toLocaleString()) : ""}${stage === "queued" ? " · 最早 " + esc(new Date(ticket.data.not_before).toLocaleString()) : ""}</p>`
          : "") +
        (meta?.refresh_required
          ? `<p>该账号有人工登记的限流，最早可在 ${esc(new Date(meta.retry_after).toLocaleString())} 核对恢复。</p>`
          : "") +
        (reasons[state.block] ? `<p>${esc(reasons[state.block])}</p>` : "") +
        '<div class="dialog-actions">' +
        (approver
          ? button("settings", policy ? "调整本地限额" : "设置本地限额")
          : "") +
        (approver && policy
          ? button("observe", "登记平台限流或恢复核对")
          : "") +
        (pub && writer && policy && !["in_handoff", "settled"].includes(stage)
          ? button("reserve", "申请或重试预留") + button("defer", "延期")
          : "") +
        (approver && stage === "held" && !state.block
          ? button("handoff", "确认开始人工交接")
          : "") +
        (ticket &&
        ((stage === "in_handoff" && approver) ||
          (["held", "queued", "stale"].includes(stage) && (writer || approver)))
          ? button(
              "release",
              stage === "in_handoff"
                ? "确认实际未提交并释放"
                : "释放未使用席位",
            )
          : "") +
        "</div>",
    );
    d.querySelector("[name=quota-account]").onchange = async (e) => {
      accountId = e.target.value;
      pubId = null;
      await draw();
    };
    if (pub)
      d.querySelector("[name=quota-pub]").onchange = async (e) => {
        pubId = e.target.value;
        await draw();
      };
    d.querySelectorAll("[data-slot]").forEach(
      (b) =>
        (b.onclick = () => {
          const act = b.dataset.slot,
            key = uid();
          const settings = act === "settings",
            observation = act === "observe";
          const input = (label, name, val, min, max) =>
            field(
              label,
              name,
              val,
              "number",
              `min="${min}" max="${max}" step="1" required`,
            );
          const fields = settings
            ? input(
                "滚动 24 小时份额",
                "daily",
                meta?.daily_slots ?? 10,
                1,
                100000,
              ) +
              input(
                "同时交接席位",
                "concurrent",
                meta?.concurrent_slots ?? 1,
                1,
                1000,
              ) +
              input(
                "未交接预留的有效秒数",
                "ttl",
                meta?.hold_seconds ?? 300,
                10,
                86400,
              )
            : observation
              ? select("本次人工核对", "outcome", [
                  ["rate_limited", "收到平台 429"],
                  ["reviewed", "等待期后已重新核对"],
                ]) +
                field(
                  "429 的等待秒数",
                  "seconds",
                  "",
                  "number",
                  'min="1" max="604800"',
                )
              : act === "defer"
                ? field(
                    "最早交接时间（含时区）",
                    "time",
                    new Date(Date.now() + 3600000).toISOString(),
                    "text",
                    "required",
                  )
                : "";
          modal(
            b.textContent,
            `<form data-form class="form-stack">${fields}${area(observation ? "核对凭据或来源" : "操作依据", "reason", "", "required")}<button type="submit" class="button primary">确认</button></form>`,
            async (f) => {
              const v = formData(f);
              const payload = settings
                ? {
                    account_id: accountId,
                    expected_revision: policy?.version || 0,
                    daily_slots: Number(v.daily),
                    concurrent_slots: Number(v.concurrent),
                    hold_seconds: Number(v.ttl),
                    reason: v.reason,
                  }
                : observation
                  ? {
                      account_id: accountId,
                      expected_revision: policy.version,
                      outcome: v.outcome,
                      retry_after_seconds:
                        v.outcome === "rate_limited" ? Number(v.seconds) : null,
                      evidence: v.reason,
                    }
                  : {
                      publication: pin(pub),
                      expected_ticket_version: ticket?.version || 0,
                      action: act,
                      reason: v.reason,
                      not_before: v.time || null,
                    };
              await ctx.api(
                apiBase +
                  "/" +
                  (settings
                    ? "settings"
                    : observation
                      ? "provider-observation"
                      : "act"),
                {
                  method: "POST",
                  body: { project_id: project, request_id: key, ...payload },
                },
              );
              await ctx.reloadData();
              await draw();
            },
          );
        }),
    );
  }
  await draw();
}

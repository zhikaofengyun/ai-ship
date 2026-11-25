import { esc } from "./ui.js";

const words = {
  not_connected: "尚未绑定",
  authorizing: "等待平台确认",
  exchanging: "正在交换授权",
  active: "只读凭据已保存",
  expired: "授权已到期，请重新取得本人授权",
  denied: "用户取消或平台拒绝",
  authorization_failed: "账号、权限或平台回执未通过核对",
  refreshing: "正在轮换令牌",
  refresh_unknown: "刷新结果待核对",
  revoking: "正在解除授权",
  revoke_unconfirmed: "撤销结果待核对，读取已停止",
  disconnected: "本地已解绑",
};

export async function mountTikTokLogin(
  ctx,
  host,
  account,
  project,
  active,
  refreshConnection,
) {
  const path =
    "/production/tiktok/login/accounts/" + encodeURIComponent(account);
  const allowed = ["owner", "reviewer"].includes(ctx.state.health.actor.role);
  async function read() {
    const v = await ctx.api(
      path + "?" + new URLSearchParams({ project_id: project }),
    );
    if (!active()) return;
    if (!v.assigned) {
      host.replaceChildren();
      return;
    }
    host.innerHTML = `<h3>Login Kit 授权</h3><p role="status">${esc(words[v.state] || v.state)}${v.access_until && v.state === "active" ? " · 访问期限 " + esc(new Date(v.access_until * 1000).toLocaleString()) : ""}</p><div class="form-actions">${allowed ? `${["not_connected", "expired", "disconnected", "denied", "authorization_failed", "authorizing", "refresh_unknown", "exchanging", "refreshing"].includes(v.state) ? '<button type="button" class="button primary" data-login="start">前往 TikTok 授权</button>' : ""}${v.state === "active" ? '<button type="button" class="button secondary" data-login="refresh">刷新只读授权</button>' : ""}${!["not_connected", "disconnected"].includes(v.state) ? '<button type="button" class="button ghost" data-login="revoke">解除 TikTok 授权</button>' : ""}` : ""}</div>`;
    for (const btn of host.querySelectorAll("[data-login]"))
      btn.onclick = async () => {
        const action = btn.dataset.login;
        if (
          action === "revoke" &&
          !window.confirm(
            "立即停止此账号的托管读取，并向 TikTok 请求撤销已保存的授权？",
          )
        )
          return;
        for (const b of host.querySelectorAll("button")) b.disabled = true;
        try {
          const reply = await ctx.api(path + "/" + action, {
            method: "POST",
            body: { project_id: project, expected_revision: v.revision },
          });
          if (!active()) return;
          if (action === "start") {
            const dest = new URL(reply.url);
            if (
              dest.origin !== "https://www.tiktok.com" ||
              dest.pathname !== "/v2/auth/authorize/"
            )
              throw new Error("平台授权入口无法核对。");
            window.location.assign(dest.href);
          } else await Promise.all([read(), refreshConnection()]);
        } catch (e) {
          if (active()) {
            await read().catch(() => {});
            ctx.toast(e.message, "error");
          }
        }
      };
  }
  try {
    await read();
  } catch (e) {
    if (active()) host.textContent = e.message;
  }
}

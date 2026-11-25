import { $, area, esc, modal, select } from "./ui.js";

export async function directoryIntake(ctx) {
  const pid = ctx.requireProject();
  const path = "/production/directory-imports";
  const { roots } = await ctx.api(
    path + "/roots?" + new URLSearchParams({ project_id: pid }),
  );
  if (ctx.state.projectId !== pid) return;
  if (!roots.length) {
    modal(
      "采集目录",
      "<p>当前项目没有登记服务端采集目录。管理员可在 AI_SHIP_CAPTURE_ROOTS 中指定根目录与项目归属。</p>",
    );
    return;
  }
  const key = "capture-intake:" + ctx.state.health.actor.tenant_id + ":" + pid;
  let prev = {};
  try {
    prev = JSON.parse(sessionStorage.getItem(key) || "{}");
  } catch {}
  const d = modal(
    "导入实拍素材",
    `<form data-intake class="form-stack">${select(
      "采集目录",
      "root",
      roots.map((r) => [
        r.alias,
        r.alias + " · 稳定观察 " + r.stable_seconds + " 秒",
      ]),
      prev.root,
    )}
    ${area("清单（可选）", "manifest", prev.manifest || "", 'placeholder="每行一个相对路径，可在制表符后附 SHA-256；留空扫描整个目录"')}
    <div class="form-actions"><button type="submit" class="button primary">观察并导入</button><button type="button" data-stop class="button secondary" hidden>停止观察</button><button type="button" data-status class="button secondary">查看已有队列</button><button type="button" data-retry class="button ghost" hidden>重试失败项</button></div></form>
    <p data-progress class="help-text" role="status"></p><div data-results></div><button type="button" data-next class="button ghost" hidden>下一页</button>`,
    null,
    true,
  );
  const token = d.dataset.modalId,
    active = () =>
      d.open && d.dataset.modalId === token && ctx.state.projectId === pid;
  const form = $("[data-intake]", d),
    stop = $("[data-stop]", d),
    retry = $("[data-retry]", d),
    next = $("[data-next]", d);
  const words = {
    waiting: "待稳定",
    imported: "已导入",
    deduplicated: "已复用",
    blocked: "已跳过",
    failed: "失败",
  };
  let running = false,
    cursor = null,
    cancel = false;
  const body = () => {
    const root = form.elements.root.value,
      raw = form.elements.manifest.value.trim();
    try {
      sessionStorage.setItem(key, JSON.stringify({ root, manifest: raw }));
    } catch {}
    return {
      project_id: pid,
      root,
      ...(raw
        ? {
            manifest: raw
              .split(/\r?\n/)
              .filter((x) => x.trim())
              .map((line) => {
                const [path, hash, ...extra] = line.split("\t");
                if (
                  extra.length ||
                  (hash && !/^[a-f0-9]{64}$/i.test(hash.trim()))
                )
                  throw new Error("每行填写相对路径，及可选的 SHA-256。");
                return {
                  path: path.trim(),
                  ...(hash ? { sha256: hash.trim().toLowerCase() } : {}),
                };
              }),
          }
        : {}),
    };
  };
  const post = (url, body) => ctx.api(path + url, { method: "POST", body });
  function show(v) {
    if (!active()) return;
    $("[data-progress]", d).textContent =
      Object.entries(v.counts)
        .map(([k, n]) => (words[k] || k) + " " + n)
        .join(" · ") || "目录清单为空。";
    $("[data-results]", d).innerHTML = v.items.length
      ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>相对路径</th><th>状态</th><th>凭据或原因</th></tr></thead><tbody>${v.items.map((r) => `<tr><td>${esc(r.path)}</td><td>${esc(words[r.state])}</td><td>${esc(r.reason || r.content_hash?.slice(0, 12) || "")}</td></tr>`).join("")}</tbody></table></div>`
      : "";
    retry.hidden = !v.counts.failed;
    cursor = v.next_after;
    next.hidden = !cursor;
  }
  const error = (e) => {
    if (active()) ctx.toast(e.message, "error");
  };
  async function run(retrying = false) {
    if (running) return;
    cancel = false;
    running = true;
    try {
      const selection = body(),
        delay = roots.find((r) => r.alias === selection.root).stable_seconds;
      for (const el of form.elements) if (el !== stop) el.disabled = true;
      stop.hidden = false;
      let v = await post("/observe", selection);
      show(v);
      let retryNow = retrying;
      while (active() && !cancel && (v.counts.waiting || retryNow)) {
        const old = v.counts.waiting;
        v = await post("/advance", {
          project_id: pid,
          root: selection.root,
          snapshot: v.snapshot,
          limit: 16,
          retry_failed: retryNow,
        });
        retryNow = false;
        show(v);
        if (v.counts.waiting && old === v.counts.waiting)
          await new Promise((resolve) =>
            setTimeout(resolve, Math.min(delay, 2) * 1000),
          );
      }
      if (active() && !cancel) await ctx.reloadData();
    } catch (e) {
      error(e);
    } finally {
      if (active()) {
        for (const el of form.elements) el.disabled = false;
        stop.hidden = true;
      }
      running = false;
    }
  }
  form.onsubmit = (e) => {
    e.preventDefault();
    run();
  };
  stop.onclick = () => {
    cancel = true;
    stop.disabled = true;
  };
  retry.onclick = () => run(true);
  const report = async (after = "") => {
    try {
      show(await post("/items?" + new URLSearchParams({ after }), body()));
    } catch (e) {
      error(e);
    }
  };
  $("[data-status]", d).onclick = () => report();
  next.onclick = () => report(cursor);
  await report();
}

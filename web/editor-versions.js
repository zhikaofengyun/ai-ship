import { btn, esc, modal, select, time, toast } from "./ui.js";
import { mediaName, selectedMedia } from "./media.js";

const canonical = (value) =>
  JSON.stringify(value, (_, v) =>
    v && typeof v === "object" && !Array.isArray(v)
      ? Object.fromEntries(
          Object.keys(v)
            .sort()
            .map((k) => [k, v[k]]),
        )
      : v,
  );
const properties = {
  source_in: "入点",
  source_out: "出点",
  timeline_start: "起点",
  track: "轨道",
  speed: "速度",
  volume: "音量",
  opacity: "透明度",
  fit: "画面适配",
  x: "横向位置",
  y: "纵向位置",
  width: "宽度",
  height: "高度",
  keyframes: "关键帧",
  asset_id: "素材",
  asset_version_id: "素材版本",
  locked: "锁定",
  shot_ref: "分镜",
};
function differences(before, after, media) {
  const rows = [];
  const add = (group, name, change) => rows.push([group, name, change]);
  for (const [label, key] of [
    ["工程名称", "name"],
    ["画幅与帧率", "format"],
    ["锁版状态", "locked"],
  ]) {
    if (canonical(before[key]) !== canonical(after[key]))
      add("工程", label, "已更改");
  }
  for (const [group, key] of [
    ["画面", "clips"],
    ["音轨", "audio_tracks"],
  ]) {
    const previous = new Map((before[key] || []).map((c) => [c.id, c]));
    const next = new Map((after[key] || []).map((c) => [c.id, c]));
    for (const id of new Set([...previous.keys(), ...next.keys()])) {
      const a = previous.get(id),
        b = next.get(id),
        c = b || a,
        source = selectedMedia(c, media);
      const name =
        (source ? mediaName(source) : group) + " · " + time(c.timeline_start);
      if (!a || !b) {
        add(group, name, a ? "已移除" : "已加入");
        continue;
      }
      const changed = Object.keys(properties)
        .filter((k) => canonical(a[k]) !== canonical(b[k]))
        .map((k) => properties[k]);
      if (changed.length) add(group, name, changed.join("、"));
    }
  }
  for (const [index, c] of (before.captions || []).entries()) {
    const b = (after.captions || [])[index];
    if (canonical(c) !== canonical(b))
      add("字幕", c.text || "字幕", b ? "已更改" : "已移除");
  }
  for (const c of (after.captions || []).slice((before.captions || []).length))
    add("字幕", c.text, "已加入");
  return rows;
}
function compareHTML(a, b, media) {
  const rows = differences(a, b, media);
  return rows.length
    ? `<div class="table-scroll"><table class="data-table"><thead><tr><th>类别</th><th>对象</th><th>变化</th></tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`
    : '<p class="dialog-desc">两个版本的剪辑设置一致。</p>';
}
export function revisionConflict(current, local, records, fork) {
  const d = modal(
    "其他人已保存新版本",
    `<p>服务器版本 ${current.version} 与本地草稿的差异</p>${compareHTML(current.data, local, records)}<div class="dialog-actions">${btn("保留本地草稿", "dismiss-conflict", "", "button secondary")}${btn("另存为独立工程", "fork-conflict", "copy", "button primary")}</div>`,
  );
  d.querySelector("[data-action=dismiss-conflict]").onclick = () => d.close();
  d.querySelector("[data-action=fork-conflict]").onclick = async (ev) => {
    ev.currentTarget.disabled = true;
    try {
      await fork();
      d.close();
    } catch (error) {
      d.querySelector("[data-action=fork-conflict]").disabled = false;
      toast(error.message, "error");
    }
  };
}
export async function showVersions(ctx, editor, restore) {
  if (!editor.record) throw new Error("请先保存工程，再比较版本。");
  const history = await ctx.api(
    `/records/${encodeURIComponent(editor.record.id)}/history`,
  );
  const list = history.items || history;
  if (!list.length) throw new Error("当前工程还没有历史版本。");
  const current =
    list.find((r) => r.version === editor.record.version) || list[0];
  const baseline = list.find((r) => r.version < current.version) || current;
  const d = modal(
    "比较剪辑版本",
    `<div class="form-grid">${select(
      "参照版本",
      "base",
      list.map((r) => [r.version, "版本 " + r.version]),
      baseline.version,
    )}${select("比较版本", "target", [["draft", "当前草稿"], ...list.map((r) => [r.version, "版本 " + r.version])], "draft")}</div><div id="edit-differences"></div><div class="dialog-actions">${btn("使用参照版本建立草稿", "restore-edit", "history", "button secondary")}</div>`,
  );
  const first = d.querySelector("[name=base]"),
    second = d.querySelector("[name=target]");
  const side = (v) => list.find((r) => r.version === Number(v));
  const paint = () => {
    d.querySelector("#edit-differences").innerHTML = compareHTML(
      side(first.value).data,
      second.value === "draft" ? editor.data : side(second.value).data,
      ctx.state.records,
    );
  };
  first.onchange = second.onchange = paint;
  paint();
  d.querySelector("[data-action=restore-edit]").onclick = () => {
    restore(side(first.value));
    d.close();
  };
}

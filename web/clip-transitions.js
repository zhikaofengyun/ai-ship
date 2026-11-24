import { esc, field, formData, modal } from "./ui.js";

export const transitionLabels = {
  dissolve: "交叉叠化",
  wipe_left: "向左擦除",
  wipe_right: "向右擦除",
  dip_black: "淡至黑色",
  dip_white: "淡至白色",
};

const epsilon = 0.00001;
const duration = (c) => (c.source_out - c.source_in) / (c.speed || 1);
const trackOf = (clips, c) =>
  clips
    .filter((item) => (item.track || 0) === (c.track || 0))
    .sort((a, b) => a.timeline_start - b.timeline_start);

export function checkTransitions(clips, fps) {
  for (const clip of clips) {
    const fx = clip.transition_in;
    if (!fx) continue;
    const lane = trackOf(clips, clip),
      prev = lane[lane.indexOf(clip) - 1];
    const start = clip.timeline_start,
      end = start + fx.duration;
    if (
      !Object.hasOwn(transitionLabels, fx.kind) ||
      !Number.isFinite(fx.duration) ||
      fx.duration <= 0 ||
      fx.duration > 30 ||
      fx.duration + epsilon < 1 / fps ||
      !prev ||
      prev.id !== fx.from_id ||
      prev.timeline_start >= start - epsilon ||
      duration(clip) <= fx.duration + epsilon ||
      Math.abs(prev.timeline_start + duration(prev) - end) > epsilon
    ) {
      throw new Error("转场两侧已改变。请在片段参数中移除或重新设置转场。");
    }
    if (
      lane.some(
        (c) =>
          c !== clip &&
          c !== prev &&
          c.timeline_start < end - epsilon &&
          c.timeline_start + duration(c) > start + epsilon,
      )
    )
      throw new Error("转场区间与第三个镜头重叠，请先调整同轨镜头。");
  }
}

export function editTransition(clips, clip, kind, seconds, fps) {
  const lane = trackOf(clips, clip),
    index = lane.indexOf(clip),
    prev = lane[index - 1];
  if (clip.locked) throw new Error("此镜头已锁定。");
  const old = clip.transition_in;
  const overlap = prev
    ? prev.timeline_start + duration(prev) - clip.timeline_start
    : 0;
  if (
    kind === "cut" &&
    old &&
    (!prev ||
      prev.id !== old.from_id ||
      Math.abs(overlap - old.duration) > epsilon)
  ) {
    delete clip.transition_in;
    return;
  }
  if (!prev) throw new Error("请先选择同轨中的第二个或后续镜头。");
  if (prev.locked) throw new Error("前一镜头已锁定。");
  if (Math.abs(overlap - (old?.duration || 0)) > epsilon)
    throw new Error("请先对齐前后镜头的剪切点，再添加转场。");
  const amount = kind === "cut" ? 0 : seconds;
  const delta = overlap - amount,
    following = lane.slice(index);
  if (Math.abs(delta) > epsilon && following.some((c) => c.locked))
    throw new Error("此调整会移动锁定的后续片段。");
  for (const c of following)
    c.timeline_start = Math.round((c.timeline_start + delta) * 1e8) / 1e8;
  if (kind === "cut") delete clip.transition_in;
  else clip.transition_in = { kind, duration: amount, from_id: prev.id };
  checkTransitions(clips, fps);
}

export function transitionForm(clips, clip, fps, apply, name) {
  const lane = trackOf(clips, clip),
    prev = lane[lane.indexOf(clip) - 1];
  if (!prev && !clip.transition_in)
    throw new Error("请选择要接入转场的后一个镜头。");
  const choices = { cut: "直接剪切", ...transitionLabels };
  const choice = clip.transition_in?.kind || "dissolve";
  modal(
    "镜头转场",
    `<form data-form class="form-stack"><strong>${esc(prev ? name(prev) : "原镜头已移除")} → ${esc(name(clip))}</strong><label class="field"><span>转场方式</span><select name="joinKind">${Object.entries(
      choices,
    )
      .map(
        ([k, v]) =>
          `<option value="${k}" ${k === choice ? "selected" : ""}>${v}</option>`,
      )
      .join(
        "",
      )}</select></label>${field("交叠时长（秒）", "seconds", clip.transition_in?.duration || Math.min(0.5, Math.max(1 / fps, Math.floor((Math.min(duration(clip), prev ? duration(prev) : 1) * fps) / 2) / fps)), "number", 'min="0" max="30" step="any" required')}<p class="help-text">同轨后续镜头会随交叠时长移动。字幕和独立音轨保留原位置。</p><div class="form-actions"><button class="button primary" type="submit">应用转场</button></div></form>`,
    (form) => {
      const values = formData(form);
      apply(values.joinKind, Number(values.seconds));
    },
  );
}

export function showTransition(element, c, clips, at, baseOpacity) {
  const fx = c.transition_in;
  const next = clips.find(
    (n) =>
      n.transition_in?.from_id === c.id &&
      at >= n.timeline_start &&
      at < n.timeline_start + n.transition_in.duration,
  );
  let alpha = baseOpacity,
    gain = 1,
    leftWipe = 0,
    rightWipe = 0,
    filter = "";
  const progress = (t, d) => Math.max(0, Math.min(1, t / d));
  if (next) {
    const p = progress(at - next.timeline_start, next.transition_in.duration);
    gain *= 1 - p;
    if (next.transition_in.kind.startsWith("dip_")) {
      const b = Math.max(0, 1 - 2 * p);
      filter =
        next.transition_in.kind === "dip_black"
          ? `brightness(${b})`
          : `invert(1) brightness(${b}) invert(1)`;
    }
  }
  const entering = fx && at < c.timeline_start + fx.duration;
  if (entering) {
    const p = progress(at - c.timeline_start, fx.duration);
    gain *= p;
    if (fx.kind === "dissolve") alpha *= p;
    else if (fx.kind === "wipe_right") rightWipe = 1 - p;
    else if (fx.kind === "wipe_left") leftWipe = 1 - p;
    else alpha *= Math.max(0, 2 * p - 1);
  }
  element.style.filter = filter;
  const [left, top, right, bottom] = c.mask?.rect || [0, 0, 1, 1];
  element.style.clipPath =
    c.mask || leftWipe || rightWipe
      ? `inset(${top * 100}% ${Math.max(1 - right, rightWipe) * 100}% ${(1 - bottom) * 100}% ${Math.max(left, leftWipe) * 100}%)`
      : "";
  element.style.opacity = alpha;
  element.style.zIndex = (Number(c.track || 0) + 1) * 2 + (entering ? 1 : 0);
  return gain;
}

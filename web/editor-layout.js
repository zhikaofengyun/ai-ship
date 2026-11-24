import { loadView, storeView, makeDivider } from "./workspace-layout.js";
import { icon } from "./ui.js";

const panels = { bin: "素材", monitor: "预览", inspector: "检查器" };
const layouts = [
  "bin monitor inspector",
  "inspector monitor bin",
  "monitor bin inspector",
  "monitor inspector bin",
  "bin inspector monitor",
  "inspector bin monitor",
];
const clamp = (n, min, max) => Math.max(min, Math.min(n, max));
export const layoutMenu = () =>
  `<details class="room-layout-menu"><summary aria-label="工作区布局" title="工作区布局">${icon("sidebar")}</summary><div class="room-layout-popover"><label>面板顺序<select data-layout-order>${layouts
    .map(
      (s) =>
        `<option value="${s}">${s
          .split(" ")
          .map((id) => panels[id])
          .join(" · ")}</option>`,
    )
    .join(
      "",
    )}</select></label><label>时间线位置<select data-layout-timeline><option value="below">下方</option><option value="above">上方</option></select></label><button type="button" data-layout-reset>恢复默认布局</button></div></details>`;

export function installEditorLayout(room, key, onResize) {
  let prefs = loadView(key),
    positions = [],
    visible = [],
    sizes = {},
    availableHeight = 0,
    upperHeight = 0;
  let recalc, mobileMeasure;
  const upper = room.querySelector(".room-upper"),
    body = room.querySelector(".room-body");
  const menu = room.querySelector(".room-layout-menu"),
    select = menu.querySelector("[data-layout-order]"),
    timeline = menu.querySelector("[data-layout-timeline]");
  const measures = [];
  const defaults = { bin: 140, inspector: 170, monitor: 230 };
  const order = () =>
    layouts.includes(prefs.order)
      ? prefs.order.split(" ")
      : layouts[0].split(" ");
  const layout = () => {
    if (!room.isConnected || (!upper.clientWidth && room.clientWidth > 780))
      return;
    const desktop = room.clientWidth > 780;
    menu.hidden = !desktop;
    select.value = order().join(" ");
    timeline.value = prefs.timeline === "above" ? "above" : "below";
    body.dataset.timelinePosition = timeline.value;
    if (!desktop) {
      if (Number.isFinite(prefs.mobileHeight))
        body.style.setProperty(
          "--mobile-view-height",
          clamp(
            prefs.mobileHeight,
            148,
            Math.max(170, Math.round(innerHeight * 0.64)),
          ) + "px",
        );
      else body.style.removeProperty("--mobile-view-height");
      mobileMeasure?.();
      return;
    }
    const width = upper.clientWidth;
    visible = order().filter(
      (id) =>
        (id !== "bin" || room.dataset.binHidden !== "true") &&
        (id !== "inspector" || room.dataset.inspectorHidden !== "true"),
    );
    const sides = visible.filter((id) => id !== "monitor");
    sizes = {
      bin: clamp(
        Math.round(Number.isFinite(prefs.bin) ? prefs.bin : width * 0.21),
        140,
        Number.isFinite(prefs.bin) ? width : 278,
      ),
      inspector: clamp(
        Math.round(
          Number.isFinite(prefs.inspector) ? prefs.inspector : width * 0.23,
        ),
        170,
        Number.isFinite(prefs.inspector) ? width : 294,
      ),
    };
    const spare =
      width - defaults.monitor - sides.reduce((n, id) => n + defaults[id], 0);
    const extra = sides.reduce((n, id) => n + sizes[id] - defaults[id], 0);
    if (extra > spare && extra > 0)
      for (const id of sides)
        sizes[id] =
          defaults[id] +
          Math.floor(((sizes[id] - defaults[id]) * Math.max(0, spare)) / extra);
    sizes.monitor = width - sides.reduce((n, id) => n + sizes[id], 0);
    upper.style.setProperty(
      "--arranged-columns",
      visible
        .map((id) => (id === "monitor" ? "minmax(0, 1fr)" : sizes[id] + "px"))
        .join(" "),
    );
    upper.style.setProperty("--arranged-areas", '"' + visible.join(" ") + '"');
    availableHeight = Math.max(
      390,
      (room.closest("main")?.clientHeight || innerHeight) -
        room.querySelector(".room-head").offsetHeight,
    );
    const defaultHeight = clamp(innerHeight - 382, 300, 790);
    upperHeight = clamp(
      Number.isFinite(prefs.upper) ? prefs.upper : defaultHeight,
      172,
      availableHeight - 138,
    );
    body.style.setProperty("--room-height", availableHeight + "px");
    upper.style.setProperty("--room-height-upper", upperHeight + "px");
    positions = visible
      .slice(1)
      .map((_, index) =>
        visible.slice(0, index + 1).reduce((n, id) => n + sizes[id], 0),
      );
    for (let i = 0; i < 2; i++) {
      const handle = upper.querySelector('[data-divider="pane-' + i + '"]');
      if (handle) {
        handle.hidden = i >= positions.length;
        if (!handle.hidden) {
          const left = visible[i],
            right = visible[i + 1];
          handle.setAttribute(
            "aria-label",
            panels[left] + "与" + panels[right] + "之间的分隔线",
          );
          handle.setAttribute(
            "aria-controls",
            [left, right]
              .map(
                (id) => upper.querySelector('[data-room-pane="' + id + '"]').id,
              )
              .join(" "),
          );
        }
        handle.style.left = (positions[i] || 0) + "px";
      }
    }
    for (const measure of measures) measure();
    onResize();
  };
  const save = () => storeView(key, prefs);
  for (let i = 0; i < 2; i++) {
    measures.push(
      makeDivider(upper, "pane-" + i, "调整相邻面板宽度", "vertical", () => {
        const left = visible[i],
          right = visible[i + 1],
          sum = (sizes[left] || 0) + (sizes[right] || 0);
        return {
          value: sizes[left] || 0,
          min: defaults[left] || 0,
          max: sum - (defaults[right] || 0),
          sign: 1,
          set(n, commit) {
            if (!left || !right) return;
            if (left !== "monitor") prefs[left] = n;
            if (right !== "monitor") prefs[right] = sum - n;
            layout();
            if (commit) save();
          },
          reset() {
            delete prefs[left];
            delete prefs[right];
            layout();
            save();
          },
        };
      }),
    );
  }
  measures.push(
    makeDivider(
      upper,
      "timeline-height",
      "调整预览与时间线高度",
      "horizontal",
      () => ({
        value: upperHeight,
        min: 172,
        max: availableHeight - 138,
        sign: timeline.value === "above" ? -1 : 1,
        set(n, commit) {
          prefs.upper = n;
          layout();
          if (commit) save();
        },
        reset() {
          delete prefs.upper;
          layout();
          save();
        },
      }),
    ),
  );
  mobileMeasure = makeDivider(
    body,
    "mobile-preview-height",
    "调整手机预览高度",
    "horizontal",
    () => ({
      value: room.querySelector(".room-monitor").getBoundingClientRect().height,
      min: 148,
      max: Math.max(170, Math.round(innerHeight * 0.64)),
      sign: 1,
      set(n, commit) {
        prefs.mobileHeight = n;
        layout();
        if (commit) save();
      },
      reset() {
        delete prefs.mobileHeight;
        layout();
        save();
      },
    }),
  );
  // Pointer capture keeps the user's next gesture independent of the browser's
  // native file/asset drag session, and also supports a pen or a touch screen.
  let dockDrag = null;
  const endTarget = (ev) =>
    document
      .elementFromPoint(ev.clientX, ev.clientY)
      ?.closest("[data-room-pane]");
  const clearTarget = () =>
    upper
      .querySelectorAll(".room-dock-target")
      .forEach((el) => el.classList.remove("room-dock-target"));
  upper.addEventListener("pointerdown", (ev) => {
    const title = ev.target.closest("[data-dock-handle]");
    if (!title || room.clientWidth <= 780 || !ev.isPrimary || ev.button !== 0)
      return;
    ev.preventDefault();
    dockDrag = {
      title,
      id: ev.pointerId,
      pane: title.closest("[data-room-pane]").dataset.roomPane,
      x: ev.clientX,
      y: ev.clientY,
    };
    title.setPointerCapture(ev.pointerId);
  });
  upper.addEventListener("pointermove", (ev) => {
    if (dockDrag?.id !== ev.pointerId) return;
    if (Math.hypot(ev.clientX - dockDrag.x, ev.clientY - dockDrag.y) < 5)
      return;
    clearTarget();
    const dst = endTarget(ev);
    if (upper.contains(dst) && dst.dataset.roomPane !== dockDrag.pane)
      dst.classList.add("room-dock-target");
  });
  const endDock = (ev) => {
    if (dockDrag?.id !== ev.pointerId) return;
    const { pane, id, title, x, y } = dockDrag;
    dockDrag = null;
    clearTarget();
    if (title.hasPointerCapture(id)) title.releasePointerCapture(id);
    const dst = endTarget(ev);
    if (
      ev.type !== "pointerup" ||
      !upper.contains(dst) ||
      Math.hypot(ev.clientX - x, ev.clientY - y) < 5
    )
      return;
    const target = dst.dataset.roomPane;
    if (target !== pane) {
      prefs.order = order()
        .map((v) => (v === target ? pane : v === pane ? target : v))
        .join(" ");
      layout();
      positionFeedback();
      save();
    }
  };
  upper.addEventListener("pointerup", endDock);
  upper.addEventListener("pointercancel", endDock);
  upper.addEventListener("lostpointercapture", endDock);
  room.addEventListener("roompanelschange", layout);
  function positionFeedback() {
    if (
      matchMedia("(prefers-reduced-motion: reduce)").matches ||
      document.documentElement.dataset.motion === "reduced"
    )
      return;
    for (const header of room.querySelectorAll(
      ".room-panel-head,.inspector-heading,.room-monitor-head,.room-timeline-head",
    )) {
      header.animate?.([{ opacity: 0.65 }, { opacity: 1 }], {
        duration: 160,
        easing: "ease-out",
      });
    }
  }
  select.onchange = () => {
    prefs.order = select.value;
    layout();
    positionFeedback();
    save();
  };
  timeline.onchange = () => {
    prefs.timeline = timeline.value;
    layout();
    positionFeedback();
    save();
  };
  menu.querySelector("[data-layout-reset]").onclick = () => {
    prefs = {};
    save();
    layout();
    menu.open = false;
    menu.querySelector("summary").focus();
  };
  menu.onkeydown = (ev) => {
    if (ev.key === "Escape") {
      menu.open = false;
      menu.querySelector("summary").focus();
      ev.stopPropagation();
    }
  };
  const outside = (ev) => {
    if (menu.open && !menu.contains(ev.target)) menu.open = false;
  };
  document.addEventListener("pointerdown", outside);
  const observer = new ResizeObserver(() => {
    cancelAnimationFrame(recalc);
    recalc = requestAnimationFrame(layout);
  });
  observer.observe(room);
  if (room.closest("main")) observer.observe(room.closest("main"));
  layout();
  return () => {
    room.removeEventListener("roompanelschange", layout);
    observer.disconnect();
    cancelAnimationFrame(recalc);
    document.removeEventListener("pointerdown", outside);
  };
}

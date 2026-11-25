// Splitter values are local viewing preferences. They never change project media.
const clamp = (n, min, max) => Math.min(max, Math.max(min, n));
export function loadView(key) {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "{}");
    return value && typeof value === "object" && !Array.isArray(value)
      ? value
      : {};
  } catch {
    return {};
  }
}
export function storeView(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* Private storage can be unavailable. */
  }
}

export function makeDivider(parent, name, label, direction, spec) {
  const el = document.createElement("div");
  el.className = "workspace-divider";
  el.dataset.divider = name;
  el.tabIndex = 0;
  el.setAttribute("role", "separator");
  el.setAttribute("aria-orientation", direction);
  el.setAttribute("aria-label", label);
  el.title = label + " · 拖动或用方向键调整，双击恢复默认";
  parent.append(el);
  const measure = () => {
    const { value, min, max } = spec();
    for (const [attr, n] of Object.entries({ now: value, min, max })) {
      el.setAttribute("aria-value" + attr, String(Math.round(n)));
    }
    el.setAttribute("aria-valuetext", Math.round(value) + " 像素");
  };
  const resize = (value, commit) => {
    const { min, max, set } = spec();
    set(clamp(Math.round(value), min, max), commit);
    measure();
  };
  let drag;
  const coordinate = (ev) =>
    direction === "vertical" ? ev.clientX : ev.clientY;
  el.onpointerdown = (ev) => {
    if (ev.button !== 0 || !ev.isPrimary) return;
    ev.preventDefault();
    el.focus({ preventScroll: true });
    const { value, sign } = spec();
    drag = { id: ev.pointerId, origin: coordinate(ev), value, sign };
    el.setPointerCapture(ev.pointerId);
    el.dataset.dragging = "true";
  };
  el.onpointermove = (ev) => {
    if (drag?.id === ev.pointerId)
      resize(drag.value + (coordinate(ev) - drag.origin) * drag.sign, false);
  };
  const finish = (ev) => {
    if (drag?.id !== ev.pointerId) return;
    resize(ev.type === "pointercancel" ? drag.value : spec().value, true);
    drag = null;
    delete el.dataset.dragging;
    if (el.hasPointerCapture(ev.pointerId))
      el.releasePointerCapture(ev.pointerId);
  };
  el.onpointerup = el.onpointercancel = el.onlostpointercapture = finish;
  el.ondblclick = () => {
    spec().reset();
    measure();
  };
  el.onkeydown = (ev) => {
    const { value, sign, min, max } = spec();
    const step = ev.shiftKey ? 32 : 8;
    const offsets =
      direction === "vertical"
        ? { ArrowLeft: -1, ArrowRight: 1 }
        : { ArrowUp: -1, ArrowDown: 1 };
    if (ev.key === "Home") resize(min, true);
    else if (ev.key === "End") resize(max, true);
    else if (offsets[ev.key])
      resize(value + sign * step * offsets[ev.key], true);
    else if (ev.key === "Enter") {
      spec().reset();
      measure();
    } else return;
    ev.preventDefault();
    ev.stopPropagation();
  };
  return measure;
}

export function installSidebarResize(shell) {
  if (!shell || shell.querySelector('[data-divider="sidebar"]')) return;
  const key = "ship-navigation-layout-v1";
  const prefs = loadView(key),
    sidebar = shell.querySelector(".sidebar");
  const mode = () =>
    window.matchMedia("(max-width:760px)").matches ? "drawer" : "desktop";
  const limits = () =>
    mode() === "drawer"
      ? [
          156,
          Math.max(156, Math.round(Math.min(470, window.innerWidth * 0.92))),
        ]
      : [144, Math.max(144, Math.min(480, window.innerWidth - 490))];
  const apply = () => {
    for (const mode of ["desktop", "drawer"]) {
      const property = "--nav-" + mode + "-width";
      if (Number.isFinite(prefs[mode]))
        shell.style.setProperty(property, prefs[mode] + "px");
      else shell.style.removeProperty(property);
    }
  };
  apply();
  const measure = makeDivider(
    shell,
    "sidebar",
    "调整导航侧栏宽度",
    "vertical",
    () => {
      const [min, max] = limits();
      return {
        value: sidebar.getBoundingClientRect().width,
        min,
        max,
        sign: 1,
        set(n, commit) {
          prefs[mode()] = n;
          apply();
          if (commit) storeView(key, prefs);
        },
        reset() {
          delete prefs[mode()];
          apply();
          storeView(key, prefs);
        },
      };
    },
  );
  measure();
  const ro = new ResizeObserver(() => {
    apply();
    measure();
  });
  ro.observe(shell);
  ro.observe(sidebar);
}

// Keep the production goal/shot list and its source/evidence monitor in the
// same view. A ratio scales naturally with an independently resized sidebar.
export function installProductionResize(work) {
  if (!work) return () => {};
  const key = "ship-production-split-v1",
    prefs = loadView(key);
  const primary = work.querySelector(".director-primary");
  if (!primary) return () => {};
  const desktop = matchMedia("(min-width: 751px)");
  const limits = () => {
    const gap = parseFloat(getComputedStyle(work).columnGap) || 0;
    const usable = Math.max(1, work.clientWidth - gap);
    return { usable, min: 250, max: Math.max(250, usable - 218), gap };
  };
  const apply = () => {
    const wide = desktop.matches && work.clientWidth >= 510;
    work.dataset.splittable = String(wide);
    if (!wide) return;
    const { usable, min, max, gap } = limits();
    const width = clamp(
      Math.round((Number.isFinite(prefs.ratio) ? prefs.ratio : 0.525) * usable),
      min,
      max,
    );
    work.style.setProperty("--production-left", width + "px");
    work.style.setProperty("--production-divider", width + gap / 2 + "px");
  };
  const measure = makeDivider(
    work,
    "production",
    "调整制作方案与预览宽度",
    "vertical",
    () => {
      const { usable, min, max } = limits();
      return {
        value: primary.getBoundingClientRect().width,
        min,
        max,
        sign: 1,
        set(n, commit) {
          prefs.ratio = n / usable;
          apply();
          if (commit) storeView(key, prefs);
        },
        reset() {
          delete prefs.ratio;
          apply();
          storeView(key, prefs);
        },
      };
    },
  );
  let frame = 0,
    lastWidth,
    lastDesktop,
    lastGap;
  const update = () => {
    frame = 0;
    const width = work.clientWidth,
      gap = limits().gap;
    if (
      width === lastWidth &&
      desktop.matches === lastDesktop &&
      gap === lastGap
    )
      return;
    lastWidth = width;
    lastDesktop = desktop.matches;
    lastGap = gap;
    apply();
    measure();
  };
  // Changing columns can rewrap text and resize the observed grid's height.
  // Keep layout writes outside the observer's current delivery cycle.
  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(update);
  };
  const ro = new ResizeObserver(schedule);
  ro.observe(work);
  // The viewport can change the CSS gap or stacking mode even at a fixed grid width.
  window.addEventListener("resize", schedule);
  update();
  return () => {
    ro.disconnect();
    window.removeEventListener("resize", schedule);
    cancelAnimationFrame(frame);
  };
}

// Supplementary inspectors share one stored layout per workspace. A compact
// window stacks the detail below the canvas and keeps the same disclosure.
export function installInspectorLayout(grid, toolbar, key, label) {
  const pane = grid.querySelector("[data-inspector]");
  const prefs = loadView(key);
  const toggle = toolbar.querySelector("[data-pane-toggle]");
  const move = toolbar.querySelector("[data-pane-move]");
  const limits = () => {
    const available = grid.clientWidth;
    const max = Math.max(250, Math.min(640, available - 304));
    return {
      min: 250,
      max,
      width: clamp(Number(prefs.width) || 320, 250, max),
    };
  };
  let measured = () => {};
  function update() {
    const wide = grid.clientWidth >= 610;
    grid.dataset.split = String(wide);
    grid.dataset.side = prefs.side === "left" ? "left" : "right";
    pane.hidden = !!prefs.hidden;
    toggle.setAttribute("aria-expanded", String(!pane.hidden));
    toggle.textContent = (pane.hidden ? "展开" : "收起") + label;
    move.hidden = !wide || pane.hidden;
    move.textContent = grid.dataset.side === "right" ? "移至左侧" : "移至右侧";
    grid.style.setProperty("--inspector-width", limits().width + "px");
    measured();
  }
  measured = makeDivider(
    grid,
    "inspector",
    "调整" + label + "宽度",
    "vertical",
    () => {
      const { min, max, width } = limits();
      return {
        value: width,
        min,
        max,
        sign: prefs.side === "left" ? 1 : -1,
        set(n, commit) {
          prefs.width = n;
          update();
          if (commit) storeView(key, prefs);
        },
        reset() {
          delete prefs.width;
          update();
          storeView(key, prefs);
        },
      };
    },
  );
  toggle.onclick = () => {
    prefs.hidden = !prefs.hidden;
    storeView(key, prefs);
    update();
  };
  move.onclick = () => {
    prefs.side = grid.dataset.side === "right" ? "left" : "right";
    storeView(key, prefs);
    update();
  };
  const observer = new ResizeObserver(update);
  observer.observe(grid);
  update();
  return () => observer.disconnect();
}

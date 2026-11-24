import {
  $,
  $$,
  esc,
  arr,
  pretty,
  icon,
  toast,
  modal,
  parseJSON,
} from "./ui.js";

// Controls are generated only from the source feature's declared operation controls.
// Empty fields remain unspecified. UI defaults never become saved business facts.
const readers = new WeakMap();
const complex = new Set(["object", "mask", "group", "expression"]);
const escapeValue = (v) =>
  esc(v == null ? "" : typeof v === "object" ? JSON.stringify(v) : v);
const numeric = (v) => (v === "" ? null : Number(v));
export function renderControls(controls, values = {}) {
  return controls
    .map(
      (c) =>
        `<section class="control-field ${["table", "rangeList", "order", "weights", "referenceList", "set", "fileSelection"].includes(c.type) ? "full" : ""}" data-control-widget="${esc(c.key)}" data-control="${esc(c.key)}" data-control-type="${esc(c.type)}"><div class="control-label"><span>${esc(c.label)}</span><span class="control-unit">${esc(c.unit || "")}</span></div><div class="control-body"></div></section>`,
    )
    .join("");
}
export function readControls(container) {
  const result = {};
  $$("[data-control-widget]", container).forEach((el) => {
    const get = readers.get(el),
      v = get?.();
    if (v !== undefined) result[el.dataset.controlWidget] = v;
  });
  return result;
}
export function mountControls(container, controls, values = {}, ctx = {}) {
  for (const c of controls) {
    const el = $(`[data-control-widget="${CSS.escape(c.key)}"]`, container);
    if (!el) continue;
    const body = $(".control-body", el);
    let value =
      values[c.key] === undefined ? undefined : structuredClone(values[c.key]);
    const key = c.key;
    let touched = false;
    const changed = () => {
      touched = true;
      el.dispatchEvent(new CustomEvent("controlchange", { bubbles: true }));
    };
    el.addEventListener("input", () => {
      touched = true;
    });
    el.addEventListener("change", () => {
      touched = true;
    });
    const setReader = (fn) =>
      readers.set(el, () =>
        !touched && value !== undefined ? structuredClone(value) : fn(),
      );
    if (c.type === "enum") {
      const opts = arr(c.options);
      body.innerHTML = `<select aria-label="${esc(c.label)}"><option value="">继承 / 未指定</option>${opts.map((v) => `<option value="${esc(v)}" ${value === v ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`;
      setReader(() => $("select", body).value || undefined);
    } else if (["number", "integer", "ratio"].includes(c.type)) {
      const min = c.min ?? c.minimum ?? c.range?.min,
        max = c.max ?? c.maximum ?? c.range?.max;
      body.innerHTML = `<div class="number-control">${c.type === "ratio" ? `<input type="range" min="${min ?? 0}" max="${max ?? 1}" step="${c.step ?? 0.01}" value="${typeof value === "number" ? value : 0}" aria-label="${esc(c.label)}滑动调整">` : ""}<input type="number" ${min != null ? `min="${min}"` : ""} ${max != null ? `max="${max}"` : ""} step="${c.step ?? (c.type === "integer" ? 1 : "any")}" value="${escapeValue(value)}" placeholder="未指定" aria-label="${esc(c.label)}精确值"></div>`;
      const input = $("input[type=number]", body),
        slider = $("input[type=range]", body);
      if (slider) {
        slider.oninput = () => (input.value = slider.value);
        input.oninput = () => (slider.value = input.value);
      }
      setReader(() => {
        if (input.value === "") return undefined;
        const n = +input.value;
        if (
          !Number.isFinite(n) ||
          (c.type === "integer" && !Number.isInteger(n))
        )
          throw new Error(
            `${c.label} 需要有效${c.type === "integer" ? "整数" : "数值"}`,
          );
        if ((min != null && n < min) || (max != null && n > max))
          throw new Error(`${c.label} 超出范围`);
        return n;
      });
    } else if (["money", "duration"].includes(c.type)) {
      const amount =
          typeof value === "number" ? value : (value?.amount ?? value?.value),
        unit = value?.currency || value?.unit || "",
        business = c.timeDomain === "business_duration";
      const units = business
        ? [
            ["seconds", "秒"],
            ["minutes", "分钟"],
            ["hours", "小时"],
            ["days", "经过日（24 小时）"],
          ]
        : [
            ["seconds", "秒"],
            ["milliseconds", "毫秒"],
            ["frames", "帧"],
          ];
      body.innerHTML = `<div class="amount-control"><input data-amount type="number" min="0" step="any" value="${escapeValue(amount)}" placeholder="未指定" aria-label="${esc(c.label)}数值">${c.type === "money" ? `<input data-currency type="text" maxlength="3" value="${esc(unit)}" placeholder="币种，如 CNY" aria-label="${esc(c.label)}币种">` : `<select aria-label="${esc(c.label)}单位">${units.map(([v, l]) => `<option value="${v}" ${unit === v ? "selected" : ""}>${l}</option>`).join("")}</select>`}</div>${business ? '<p class="control-help">此处为业务经过时长，不使用视频帧。按自然月等日历规则计算的截止时间请在相关业务记录中明确规则和时区。</p>' : ""}`;
      setReader(() => {
        const n = $("[data-amount]", body).value,
          u =
            c.type === "money"
              ? $("[data-currency]", body).value.trim().toUpperCase()
              : $("select", body).value;
        if (n === "") return undefined;
        if (+n < 0 || !Number.isFinite(+n))
          throw new Error(`${c.label} 需要非负有限数值`);
        if (c.type === "money" && !/^[A-Z]{3}$/.test(u))
          throw new Error(`${c.label} 需要明确三位币种代码`);
        return c.type === "money"
          ? { amount: +n, currency: u }
          : {
              value: +n,
              unit: u,
              ...(c.timeDomain ? { time_domain: c.timeDomain } : {}),
            };
      });
    } else if (c.type === "reference") {
      const opts = ctx.records || [],
        savedId = typeof value === "string" ? value : value?.id,
        current = opts.find((r) => r.id === savedId),
        pinned = value?.version,
        stale = current && pinned && pinned !== current.version;
      body.innerHTML = `<select aria-label="${esc(c.label)}"><option value="">暂不绑定</option>${savedId && !current ? `<option value="${esc(savedId)}" selected>已保存引用（当前不可用）</option>` : ""}${opts.map((r) => `<option value="${esc(r.id)}" ${savedId === r.id ? "selected" : ""}>${esc(r.data?.name || r.data?.title || r.data?.label || r.kind)} · ${esc(r.kind)} ${savedId === r.id && pinned ? `固定 v${pinned}` : `v${r.version}`}</option>`).join("")}</select>${stale ? `<p class="control-help" data-reference-note>已固定 v${pinned}，当前版本为 v${current.version}。未确认前保留原版本，运行时重新检查。</p><button type="button" data-upgrade-ref class="button small secondary">明确改用 v${current.version}</button>` : savedId && !current ? '<p class="control-help">此引用当前不可用，保留其原始信息。请核对权限、删除或其他失效原因。</p>' : ""}`;
      $("[data-upgrade-ref]", body)?.addEventListener("click", () => {
        changed();
        $("[data-reference-note]", body).textContent =
          `已选择 v${current.version}，保存后生效。`;
      });
      setReader(() => {
        const selected = $("select", body).value,
          r = opts.find((r) => r.id === selected);
        return r
          ? { id: r.id, kind: r.kind, version: r.version }
          : selected === savedId
            ? value
            : undefined;
      });
    } else if (
      ["set", "order", "referenceList", "fileSelection"].includes(c.type)
    ) {
      let list = Array.isArray(value)
        ? value
        : typeof value === "string"
          ? [value]
          : [];
      const refMode = c.type === "referenceList" || c.type === "fileSelection";
      const ordered = c.type === "order";
      const opts = refMode
        ? (ctx.records || []).map((r) => ({
            id: r.id,
            label: `${r.data?.name || r.data?.title || r.kind} · ${r.kind}`,
          }))
        : arr(c.options).map((o) => ({ id: o, label: o }));
      const label = (v) =>
        typeof v === "object"
          ? (ctx.records || []).find((r) => r.id === v.id)?.data?.name ||
            v.kind ||
            "已绑定记录"
          : v;
      function drawList() {
        body.innerHTML = `<div class="control-chips">${list.map((v, i) => `<span class="control-chip"><span>${esc(label(v))}</span>${ordered ? `<button type="button" data-up="${i}" aria-label="上移 ${esc(label(v))}" ${i === 0 ? "disabled" : ""}>↑</button><button type="button" data-down="${i}" aria-label="下移 ${esc(label(v))}" ${i === list.length - 1 ? "disabled" : ""}>↓</button>` : ""}<button type="button" data-remove="${i}" aria-label="移除 ${esc(label(v))}">${icon("close")}</button></span>`).join("") || '<span class="control-placeholder">尚未选择</span>'}</div><div class="control-add">${opts.length ? `<select aria-label="添加${esc(c.label)}"><option value="">选择要添加的项</option>${opts.map((o) => `<option value="${esc(o.id)}">${esc(o.label)}</option>`).join("")}</select>` : `<input type="text" placeholder="添加一个值" aria-label="添加${esc(c.label)}">`}<button type="button" data-add class="button small secondary">${icon("plus")}添加</button>${c.type === "fileSelection" ? '<button type="button" data-files class="button small secondary">选择文件</button><input type="file" multiple hidden>' : ""}</div>`;
        const add = () => {
          const input = $("select,input[type=text]", body),
            v = input?.value.trim();
          if (!v) return;
          const r = refMode
              ? (ctx.records || []).find((r) => r.id === v)
              : null,
            next = r ? { id: r.id, kind: r.kind, version: r.version } : v;
          if (list.some((x) => JSON.stringify(x) === JSON.stringify(next)))
            return;
          list.push(next);
          drawList();
          changed();
        };
        $("[data-add]", body).onclick = add;
        $("input[type=text]", body)?.addEventListener("keydown", (e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add();
          }
        });
        $$("[data-remove]", body).forEach(
          (b) =>
            (b.onclick = () => {
              list.splice(+b.dataset.remove, 1);
              drawList();
              changed();
            }),
        );
        $$("[data-up]", body).forEach(
          (b) =>
            (b.onclick = () => {
              const i = +b.dataset.up;
              [list[i - 1], list[i]] = [list[i], list[i - 1]];
              drawList();
              changed();
            }),
        );
        $$("[data-down]", body).forEach(
          (b) =>
            (b.onclick = () => {
              const i = +b.dataset.down;
              [list[i], list[i + 1]] = [list[i + 1], list[i]];
              drawList();
              changed();
            }),
        );
        if (c.type === "fileSelection") {
          $("[data-files]", body).onclick = () => {
            $("input[type=file]", body).click();
          };
          $("input[type=file]", body).onchange = async (event) => {
            try {
              if (!ctx.importFiles)
                throw new Error("请从素材库导入文件后选择引用");
              const refs = await ctx.importFiles([...event.target.files]);
              list.push(...refs);
              drawList();
              changed();
              toast("文件已实际入库并绑定引用");
            } catch (err) {
              toast(err.message, "error");
            }
          };
        }
      }
      drawList();
      setReader(() => (list.length ? list : undefined));
    } else if (["table", "weights"].includes(c.type)) {
      let columns = arr(c.fields)
        .map((f) => (typeof f === "string" ? f : f.key || f.label))
        .filter(Boolean);
      let rows;
      if (c.type === "weights") {
        columns = ["名称", "权重"];
        rows = Array.isArray(value)
          ? value
          : typeof value === "object" && value
            ? Object.entries(value).map(([k, v]) => ({ 名称: k, 权重: v }))
            : [];
      } else {
        rows = Array.isArray(value)
          ? value
          : typeof value === "object" && value
            ? [value]
            : [];
        if (!columns.length)
          columns = [...new Set(rows.flatMap((r) => Object.keys(r || {})))];
      }
      function table() {
        body.innerHTML = `${columns.length ? `<div class="typed-table-scroll"><table class="typed-table"><thead><tr>${columns.map((col) => `<th>${esc(col)}</th>`).join("")}<th></th></tr></thead><tbody>${rows.map((row, i) => `<tr>${columns.map((col) => `<td><input data-row="${i}" data-col="${esc(col)}" type="${c.type === "weights" && col === "权重" ? "number" : "text"}" step="any" value="${escapeValue(row?.[col])}" placeholder="未指定" aria-label="第 ${i + 1} 行 ${esc(col)}"></td>`).join("")}<td><button type="button" class="icon-button" data-del-row="${i}" aria-label="删除第 ${i + 1} 行">${icon("close")}</button></td></tr>`).join("")}</tbody></table></div>` : '<p class="control-placeholder">暂无字段</p>'}<div class="control-add"><button type="button" class="button small secondary" data-add-row>${icon("plus")}添加行</button>${c.type === "weights" ? "" : `<button type="button" class="button small ghost" data-add-col>添加字段</button>`}</div>`;
        $$("[data-row]", body).forEach(
          (input) =>
            (input.oninput = () => {
              const text = input.value;
              rows[+input.dataset.row][input.dataset.col] =
                input.type === "number" ? (text === "" ? null : +text) : text;
            }),
        );
        $$("[data-del-row]", body).forEach(
          (b) =>
            (b.onclick = () => {
              rows.splice(+b.dataset.delRow, 1);
              table();
              changed();
            }),
        );
        $("[data-add-row]", body).onclick = () => {
          if (!columns.length) {
            addColumn();
            return;
          }
          rows.push(Object.fromEntries(columns.map((k) => [k, null])));
          table();
          changed();
        };
        $("[data-add-col]", body)?.addEventListener("click", addColumn);
      }
      function addColumn() {
        modal(
          "添加配置字段",
          `<form data-form class="form-stack"><label class="field"><span>字段名称</span><input name="column" required maxlength="100" placeholder="使用团队明确约定的字段名"></label><div class="form-actions"><button type="submit" class="button primary">添加字段</button></div></form>`,
          async (f) => {
            const col = new FormData(f).get("column").trim();
            if (
              !col ||
              ["__proto__", "constructor", "prototype"].includes(col) ||
              columns.includes(col)
            )
              throw new Error("字段不能为空或重复");
            columns.push(col);
            rows.forEach((row) => (row[col] = null));
            table();
            changed();
          },
        );
      }
      table();
      setReader(() => {
        if (!rows.length) return undefined;
        if (c.type === "weights") {
          const result = {};
          for (const row of rows) {
            if (!row["名称"]) throw new Error(`${c.label} 的权重项需要名称`);
            if (
              row["权重"] == null ||
              row["权重"] === "" ||
              !Number.isFinite(+row["权重"]) ||
              +row["权重"] < 0
            )
              throw new Error(
                `${c.label} 的权重需要明确填写非负数字，空值不能当成零`,
              );
            const name = String(row["名称"]).trim();
            if (
              !name ||
              ["__proto__", "constructor", "prototype"].includes(name) ||
              Object.hasOwn(result, name)
            )
              throw new Error(`${c.label} 的权重名称不能为空或重复`);
            result[name] = +row["权重"];
          }
          return result;
        }
        return rows;
      });
    } else if (c.type === "timecode") {
      let mode =
        typeof value === "string"
          ? "notation"
          : typeof value === "number"
            ? "legacy"
            : "ticks";
      const draft = typeof value === "object" && value ? { ...value } : {};
      let notation = typeof value === "string" ? value : "";
      function drawPoint() {
        body.innerHTML = `<label class="field"><span>时间表示</span><select data-mode><option value="ticks" ${mode === "ticks" ? "selected" : ""}>整数 ticks 与音轨时基</option><option value="notation" ${mode === "notation" ? "selected" : ""}>时码（分:秒或时:分:秒）</option>${mode === "legacy" ? '<option value="legacy" selected>既有数值 · 时基待核对</option>' : ""}</select></label>${
          mode === "ticks"
            ? `<div class="form-grid">${[
                ["ticks", "音轨 ticks"],
                ["timebase_num", "时基分子"],
                ["timebase_den", "时基分母"],
              ]
                .map(
                  ([k, l]) =>
                    `<label class="field"><span>${l}</span><input data-point="${k}" type="number" step="1" ${k === "ticks" ? "" : 'min="1"'} value="${escapeValue(draft[k])}" placeholder="明确填写" aria-label="${l}"></label>`,
                )
                .join("")}</div>`
            : `<input data-notation type="text" value="${esc(mode === "legacy" ? value : notation)}" placeholder="例如 00:12.500" aria-label="${esc(c.label)}时码"><p class="control-help">此时间点属于实际音乐音轨；执行前仍须绑定音轨与其时基。</p>`
        }`;
        $("[data-mode]", body).onchange = (ev) => {
          mode = ev.target.value;
          changed();
          drawPoint();
        };
        $$("[data-point]", body).forEach(
          (input) =>
            (input.oninput = () =>
              (draft[input.dataset.point] = numeric(input.value))),
        );
        $("[data-notation]", body)?.addEventListener(
          "input",
          (ev) => (notation = ev.target.value),
        );
      }
      drawPoint();
      setReader(() => {
        if (mode === "ticks") {
          if (Object.values(draft).every((v) => v == null)) return undefined;
          if (
            !Number.isInteger(draft.ticks) ||
            !Number.isInteger(draft.timebase_num) ||
            draft.timebase_num <= 0 ||
            !Number.isInteger(draft.timebase_den) ||
            draft.timebase_den <= 0
          )
            throw new Error(`${c.label} 需要整数 ticks 与正整数时基`);
          return { ...draft, time_domain: c.timeDomain || "media_point" };
        }
        if (!notation.trim()) return undefined;
        if (mode === "legacy")
          throw new Error("修改既有时间点时请明确选择时码或 ticks 与时基");
        if (
          !/^(?:[0-9]+:)?[0-5]?[0-9]:[0-5][0-9](?:[.:][0-9]+)?$/.test(notation)
        )
          throw new Error("时码格式应为 MM:SS(.ms) 或 HH:MM:SS(.ms)");
        return notation.trim();
      });
    } else if (c.type === "timeRange" && c.timeDomain) {
      const range =
          typeof value === "object" && !Array.isArray(value) && value
            ? { ...value }
            : {},
        absolute = ["authorization_timestamp", "approval_timestamp"].includes(
          c.timeDomain,
        );
      let mode =
        absolute || String(range.start || "").includes("T")
          ? "timestamp"
          : "date";
      function drawCalendar() {
        body.innerHTML = `<div class="form-stack">${!absolute ? `<label class="field"><span>原始时间语义</span><select data-calendar-mode><option value="date" ${mode === "date" ? "selected" : ""}>业务日期边界</option><option value="timestamp" ${mode === "timestamp" ? "selected" : ""}>带时区的绝对时刻</option></select></label>` : ""}<div class="form-grid">${[
          ["start", "开始"],
          ["end", "结束"],
        ]
          .map(
            ([k, l]) =>
              `<label class="field"><span>${l}</span><input data-calendar="${k}" type="${mode === "date" ? "date" : "text"}" value="${escapeValue(range[k])}" placeholder="${mode === "date" ? "YYYY-MM-DD" : "2026-11-24T09:00:00+08:00"}" aria-label="${esc(c.label)}${l}"></label>`,
          )
          .join(
            "",
          )}</div><label class="field"><span>来源或适用时区</span><input data-calendar="timezone" value="${escapeValue(range.timezone)}" placeholder="例如 Asia/Shanghai" aria-label="${esc(c.label)}时区"></label><label class="field"><span>边界含义</span><select data-calendar="boundary"><option value="">依来源明确选择</option>${[
          ["[start,end]", "含起点与终点"],
          ["[start,end)", "含起点，不含终点"],
          ["(start,end]", "不含起点，含终点"],
          ["(start,end)", "不含起点与终点"],
        ]
          .map(
            ([v, l]) =>
              `<option value="${v}" ${range.boundary === v ? "selected" : ""}>${l}</option>`,
          )
          .join(
            "",
          )}</select></label></div><p class="control-help">保留 ${esc(c.timeDomain)} 的日历或绝对时间含义；不转换为媒体 ticks。</p>`;
        $$("[data-calendar]", body).forEach(
          (input) =>
            (input.oninput = () =>
              (range[input.dataset.calendar] = input.value)),
        );
        $("[data-calendar-mode]", body)?.addEventListener("change", (ev) => {
          mode = ev.target.value;
          changed();
          drawCalendar();
        });
      }
      drawCalendar();
      setReader(() => {
        if (!range.start && !range.end && !range.timezone && !range.boundary)
          return undefined;
        if (!range.start || !range.end || !range.timezone || !range.boundary)
          throw new Error(`${c.label} 需要完整起止值、时区和边界含义`);
        try {
          new Intl.DateTimeFormat("en", { timeZone: range.timezone });
        } catch {
          throw new Error(`${c.label} 时区无效`);
        }
        if (
          mode === "timestamp" &&
          (!/(?:Z|[+-]\d{2}:\d{2})$/.test(range.start) ||
            !/(?:Z|[+-]\d{2}:\d{2})$/.test(range.end))
        )
          throw new Error(`${c.label} 的绝对时刻必须包含时区偏移或 Z`);
        const a = Date.parse(range.start),
          b = Date.parse(range.end);
        if (!Number.isFinite(a) || !Number.isFinite(b) || b < a)
          throw new Error(`${c.label} 起止时间无效`);
        return {
          ...range,
          start: absolute ? new Date(a).toISOString() : range.start,
          end: absolute ? new Date(b).toISOString() : range.end,
          time_domain: c.timeDomain,
        };
      });
    } else if (["rangeList", "timeRange", "range"].includes(c.type)) {
      let rows =
        Array.isArray(value) &&
        value.length === 2 &&
        value.every((v) => typeof v === "number")
          ? [{ start: value[0], end: value[1] }]
          : Array.isArray(value)
            ? value
            : value
              ? [value]
              : [];
      const temporal = c.type !== "range";
      function drawRanges() {
        body.innerHTML = `<div class="control-ranges">${
          rows
            .map(
              (r, i) =>
                `<div class="control-range-row"><label><span>${temporal ? "起点 ticks" : "最小值"}</span><input data-i="${i}" data-k="start" type="number" step="${temporal ? "1" : "any"}" value="${escapeValue(r.start)}" aria-label="第${i + 1}段起点"></label><span class="muted">—</span><label><span>${temporal ? "终点 ticks" : "最大值"}</span><input data-i="${i}" data-k="end" type="number" step="${temporal ? "1" : "any"}" value="${escapeValue(r.end)}" aria-label="第${i + 1}段终点"></label>${
                  temporal
                    ? `<label><span>时间域</span><select data-i="${i}" data-k="clock_domain">${[
                        ["source", "源素材"],
                        ["output", "输出"],
                        ["sequence", "序列"],
                      ]
                        .map(
                          ([v, l]) =>
                            `<option value="${v}" ${r.clock_domain === v ? "selected" : ""}>${l}</option>`,
                        )
                        .join(
                          "",
                        )}</select></label><label><span>时基分子</span><input data-i="${i}" data-k="timebase_num" type="number" min="1" step="1" value="${r.timebase_num ?? ""}" placeholder="如 1" aria-label="时基分子"></label><label><span>时基分母</span><input data-i="${i}" data-k="timebase_den" type="number" min="1" step="1" value="${r.timebase_den ?? ""}" placeholder="如 1000" aria-label="时基分母"></label>`
                    : ""
                }<button type="button" class="icon-button" data-del-range="${i}" aria-label="删除区间">${icon("close")}</button></div>`,
            )
            .join("") || '<span class="control-placeholder">尚未指定区间</span>'
        }</div><button type="button" class="button small secondary" data-add-range>${icon("plus")}添加区间</button>`;
        $$("[data-i]", body).forEach(
          (input) =>
            (input.oninput = () => {
              rows[+input.dataset.i][input.dataset.k] =
                input.tagName === "SELECT" ? input.value : numeric(input.value);
            }),
        );
        $$("[data-del-range]", body).forEach(
          (b) =>
            (b.onclick = () => {
              rows.splice(+b.dataset.delRange, 1);
              drawRanges();
              changed();
            }),
        );
        $("[data-add-range]", body).onclick = () => {
          if (c.type === "timeRange" && rows.length) {
            toast("该参数只接受一个区间", "error");
            return;
          }
          rows.push(
            temporal
              ? {
                  start: null,
                  end: null,
                  clock_domain: "source",
                  timebase_num: null,
                  timebase_den: null,
                }
              : { start: null, end: null },
          );
          drawRanges();
          changed();
        };
      }
      drawRanges();
      setReader(() => {
        if (!rows.length) return undefined;
        for (const r of rows) {
          if (
            r.start == null ||
            r.end == null ||
            (temporal ? r.end <= r.start : r.end < r.start)
          )
            throw new Error(`${c.label} 每个区间必须有有效起止值`);
          if (
            temporal &&
            (!Number.isInteger(r.start) ||
              !Number.isInteger(r.end) ||
              !Number.isInteger(r.timebase_num) ||
              r.timebase_num <= 0 ||
              !Number.isInteger(r.timebase_den) ||
              r.timebase_den <= 0)
          )
            throw new Error(`${c.label} 使用整数 ticks 和正时基`);
        }
        return c.type === "rangeList" ? rows : rows[0];
      });
    } else if (c.type === "schedule") {
      const v = value || {};
      body.innerHTML = `<div class="form-grid"><label class="field"><span>本地日期与时间</span><input type="datetime-local" value="${esc(v.local_datetime || "")}" data-schedule="local_datetime"></label><label class="field"><span>时区</span><input type="text" value="${esc(v.timezone || "")}" placeholder="例如 Asia/Shanghai" data-schedule="timezone"></label></div>`;
      setReader(() => {
        const date = $("[data-schedule=local_datetime]", body).value,
          zone = $("[data-schedule=timezone]", body).value;
        if (!date && !zone) return undefined;
        if (!date || !zone) throw new Error(`${c.label} 需要日期与明确时区`);
        return { ...v, local_datetime: date, timezone: zone };
      });
    } else if (c.type === "pair" || c.type === "region") {
      const pair = c.type === "pair",
        keys = arr(c.fields)
          .map((f) => (typeof f === "string" ? f : f.key || f.label))
          .filter(Boolean);
      if (!keys.length)
        keys.push(
          ...(pair
            ? ["music_volume", "original_volume"]
            : ["x", "y", "width", "height"]),
        );
      const min = Array.isArray(c.range) ? c.range[0] : c.range?.min,
        max = Array.isArray(c.range) ? c.range[1] : c.range?.max;
      body.innerHTML = `<div class="form-grid">${keys.map((k, i) => `<label class="field"><span>${esc(pair ? ["平台音乐", "原始声音"][i] || k : k)}</span><input data-key="${esc(k)}" value="${escapeValue(Array.isArray(value) ? value[i] : value?.[k])}" type="number" step="any" ${min != null ? `min="${min}"` : ""} ${max != null ? `max="${max}"` : ""} placeholder="未指定" aria-label="${esc(c.label)} ${esc(k)}"></label>`).join("")}</div>`;
      setReader(() => {
        const out = {};
        for (const input of $$("[data-key]", body)) {
          if (input.value === "") continue;
          const n = +input.value;
          if (
            !Number.isFinite(n) ||
            (min != null && n < min) ||
            (max != null && n > max)
          )
            throw new Error(`${c.label} 数值超出明确范围`);
          out[input.dataset.key] = n;
        }
        if (pair && Object.keys(out).length && Object.keys(out).length !== 2)
          throw new Error(`${c.label} 需要分别填写两个明确数值`);
        return Object.keys(out).length ? out : undefined;
      });
    } else if (complex.has(c.type)) {
      body.innerHTML = `<div class="advanced-control"><span class="control-placeholder" data-summary>${value === undefined ? "尚未设置" : `已配置 ${typeof value === "object" ? Object.keys(value).length : 1} 项`}</span><button type="button" class="button small secondary" data-edit-advanced>${icon("sliders")}编辑${c.type === "mask" ? "区域定义" : "结构化配置"}</button></div>`;
      $("[data-edit-advanced]", body).onclick = () =>
        modal(
          c.label,
          `<p class="dialog-desc">${esc(c.impact || "按此功能的输入输出合同配置。")}</p><form data-form class="form-stack"><label class="field"><span>${c.type === "expression" ? "表达式" : "高级结构化定义"}</span><textarea name="value" class="code-input" rows="12" placeholder="${c.type === "expression" ? "" : esc(pretty(c.fields ? Object.fromEntries(c.fields.map((x) => [x, null])) : {}))}">${esc(value === undefined ? "" : typeof value === "string" ? value : pretty(value))}</textarea></label><div class="form-actions"><button type="submit" class="button primary">应用配置</button></div></form>`,
          async (f) => {
            const text = new FormData(f).get("value").trim();
            value = text
              ? c.type === "expression"
                ? text
                : parseJSON(text)
              : undefined;
            $("[data-summary]", body).textContent =
              value === undefined ? "尚未设置" : "已配置，保存后生效";
            changed();
          },
        );
      setReader(() => value);
    } else {
      body.innerHTML = `${c.type === "text" ? `<textarea rows="3" aria-label="${esc(c.label)}" placeholder="未指定">${escapeValue(value)}</textarea>` : `<input type="text" value="${escapeValue(value)}" placeholder="${c.type === "datetime" ? "带时区的日期时间" : c.type === "timezone" ? "例如 Asia/Shanghai" : "未指定"}" aria-label="${esc(c.label)}">`}`;
      setReader(() => $("input,textarea", body).value.trim() || undefined);
    }
  }
}

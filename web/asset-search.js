import { $, esc, field, modal, select, toast, uid } from "./ui.js";
import { installInspectorLayout } from "./workspace-layout.js";

export async function openAssetSearch(ctx, choose) {
  const project = ctx.requireProject();
  const via = "/production/asset-index";
  const post = (route, data) =>
    ctx.api(via + route, {
      method: "POST",
      body: { project_id: project, ...data },
    });
  const dlg = modal(
    "检索素材",
    `<form class="director-index-query form-grid">
    ${field("名称或镜头标注", "text", "", "search", 'maxlength="300" autocomplete="off"')}
    <div class="director-numbers">${select(
      "媒体",
      "media_type",
      [
        ["any", "全部"],
        ["video", "视频"],
        ["image", "图片"],
        ["audio", "音频"],
      ],
      typeof choose === "function" ? "video" : "any",
    )}
    ${select(
      "检索方式",
      "channel",
      [
        ["indexed", "项目索引"],
        ["source", "直接查素材"],
        ["semantic", "语义召回"],
        ["visual", "画面语义（CLIP）"],
      ],
      "indexed",
    )}</div>
    <button class="button primary" type="submit">查找</button><div data-visual-threshold hidden>${field("最低画面相似度", "visual_threshold", "0.20", "number", 'min="-1" max="1" step="0.01" required disabled')}</div></form>
    <div class="module-layout-tools" data-search-layout-tools role="group" aria-label="素材预览布局"><button type="button" class="button ghost small" data-pane-toggle aria-controls="indexed-preview">收起素材预览</button><button type="button" class="button ghost small" data-pane-move>移至左侧</button><button type="button" class="button ghost small" data-pane-reset>还原布局</button></div>
    <div class="adjustable-layout indexed-layout"><div class="index-results"><p role="status" data-index-status></p><ul class="director-index-hits" data-hits></ul><div data-result-pages class="form-actions"></div></div><aside class="indexed-preview" id="indexed-preview" aria-label="素材预览" data-inspector><h3>素材预览</h3><p>选择搜索结果中的素材进行预览。</p></aside></div>
    <details class="director-format"><summary>索引版本</summary><div data-index-history></div></details>`,
    null,
    true,
  );
  const actor = ctx.state.health?.actor;
  const pane = $(".indexed-preview", dlg);
  const emptyPreview = pane.innerHTML;
  let selectedItem = null;
  const paneToggle = $("[data-pane-toggle]", dlg);
  const releaseLayout = installInspectorLayout(
    $(".indexed-layout", dlg),
    $("[data-search-layout-tools]", dlg),
    JSON.stringify([
      "ship-source-preview-v1",
      actor?.tenant_id || "local",
      actor?.actor_id || "owner",
      project,
    ]),
    "素材预览",
    { width: 300, min: 230, max: 500 },
  );
  const anchor = dlg.dataset.modalId,
    valid = () =>
      dlg.open &&
      dlg.dataset.modalId === anchor &&
      ctx.state.projectId === project;
  paneToggle.addEventListener("click", () => {
    if (pane.hidden) pane.querySelector("video,audio")?.pause();
  });
  let previewSerial = 0,
    frameAfter = "";
  let analyzing = false,
    stopAnalysis = false;
  let history,
    serial = 0,
    last = null,
    busy = false,
    staleActivation = false;
  const analysisStatus = document.createElement("p");
  analysisStatus.setAttribute("role", "status");
  const status = $("[data-index-status]", dlg),
    form = $(".director-index-query", dlg);
  form.elements.channel.addEventListener("change", () => {
    const visual = form.elements.channel.value === "visual";
    form.querySelector("[data-visual-threshold]").hidden = !visual;
    form.elements.visual_threshold.disabled = !visual;
    form.elements.text.placeholder =
      form.elements.channel.value === "visual"
        ? "例：a bee visiting a flower"
        : "";
  });
  async function loadHistory() {
    const v = await ctx.api(
      via + "/history?project_id=" + encodeURIComponent(project),
    );
    if (!valid()) return;
    history = v;
    const canWrite = !["viewer", "reviewer"].includes(
      ctx.state.health?.actor?.role,
    );
    const indexTools = $("[data-index-history]", dlg);
    indexTools.innerHTML = `<div class="director-actions">${canWrite ? `<button type="button" class="button small" data-analyze>批量分析素材</button><button type="button" class="button ghost small" data-stop-analysis hidden>停止后续分析</button><button type="button" class="button small" data-build="shadow">构建新索引</button><button type="button" class="button small" data-embed>构建语义索引</button><button type="button" class="button small" data-frame-build>提取画面语义索引</button>${v.head.generation ? '<button type="button" class="button small" data-build="incremental">同步变化</button>' : ""}` : ""}</div>${v.snapshots.map((r, i) => `<div class="director-index-version"><span>${esc(new Date(r.stamp).toLocaleString())} · ${r.checked} 份素材${r.reused ? " · 复用 " + r.reused : ""}</span>${r.id === v.head.generation ? "<strong>正在使用</strong>" : canWrite ? `<button type="button" class="button ghost small" data-switch="${i}">${r.previously_active ? "回退到此版本" : "启用此版本"}</button>` : ""}</div>`).join("")}`;
    if (canWrite) {
      const q = v.analysis_queue || {};
      if (!analysisStatus.textContent && Object.keys(q).length)
        analysisStatus.textContent =
          `自动分析已完成 ${(q.completed || 0) + (q.measured || 0)} 份，待处理 ${(q.queued || 0) + (q.working || 0)} 份。` +
          (q.failed ? ` ${q.failed} 项失败，可用批量分析重试。` : "");
      indexTools.append(analysisStatus);
      if (staleActivation) {
        const rebuild = document.createElement("button");
        rebuild.type = "button";
        rebuild.className = "button small";
        rebuild.dataset.rebuildActive = "";
        rebuild.textContent = "按最新素材重建并启用";
        indexTools.append(rebuild);
      }
    }
  }
  async function search(offset = 0) {
    const ticket = ++serial;
    const fields = Object.fromEntries(new FormData(form));
    status.textContent = "正在查找…";
    let v;
    try {
      v = await post("/query", {
        ...fields,
        offset,
        expected_view: offset ? last?.view : null,
      });
    } catch (error) {
      if (
        offset &&
        error.code === "RESULT_SET_CHANGED" &&
        valid() &&
        ticket === serial
      )
        return search();
      throw error;
    }
    if (!valid() || ticket !== serial) return;
    last = v;
    if (
      selectedItem &&
      !v.items.some(
        (r) =>
          r.version_id === selectedItem.version_id &&
          r.revision === selectedItem.revision &&
          r.frame_match?.sample_rgb_sha256 ===
            selectedItem.frame_match?.sample_rgb_sha256,
      )
    ) {
      pane.innerHTML = emptyPreview;
      selectedItem = null;
    }
    status.textContent =
      (v.fallback ? v.fallback + " " : "") +
      v.count +
      " 份匹配素材" +
      (v.count > v.items.length
        ? " · 当前 " + (v.offset + 1) + "–" + (v.offset + v.items.length)
        : "");
    $("[data-result-pages]", dlg).innerHTML =
      (offset
        ? '<button type="button" data-prev class="button small">上一页</button>'
        : "") +
      (v.next_offset != null
        ? '<button type="button" data-next class="button small">下一页</button>'
        : "");
    $("[data-hits]", dlg).innerHTML = v.items
      .map(
        (r, i) =>
          `<li><div><strong>${esc(r.name)}</strong><small>${r.seconds != null ? Number(r.seconds).toFixed(2) + " 秒 · " : ""}${esc([...new Set(r.tags.map((t) => t.label))].slice(0, 6).join("、") || "原片")}${Number.isFinite(r.semantic_score) && v.vector_available && (v.requested_channel !== "visual" || r.frame_match) ? (r.frame_match ? " · 画面 " + r.frame_match.source_seconds.toFixed(2) + " 秒 · 相似度 " : " · 语义相似度 ") + r.semantic_score.toFixed(3) : ""}</small></div><button type="button" class="button ghost small" data-preview="${i}" aria-label="预览 ${esc(r.name)}">预览</button>${typeof choose === "function" && r.media_type === "video" ? `<button type="button" class="button secondary small" data-choose="${i}">加入制作</button>` : ""}</li>`,
      )
      .join("");
  }
  form.onsubmit = (e) => {
    e.preventDefault();
    search().catch((e) => {
      if (valid()) status.textContent = e.message;
    });
  };
  dlg.addEventListener("click", handle);
  dlg.addEventListener(
    "close",
    () => {
      dlg.removeEventListener("click", handle);
      releaseLayout();
      pane.replaceChildren();
    },
    { once: true },
  );
  async function handle(event) {
    const b = event.target.closest("button");
    if (!b || !valid()) return;
    if (b.hasAttribute("data-stop-analysis")) {
      stopAnalysis = true;
      b.disabled = true;
      return;
    }
    if (busy || analyzing) return;
    try {
      if (b.hasAttribute("data-next") || b.hasAttribute("data-prev")) {
        if (last)
          await search(
            b.hasAttribute("data-next")
              ? last.next_offset
              : Math.max(0, last.offset - 30),
          );
        return;
      }
      if (b.hasAttribute("data-preview")) {
        const item = last?.items[Number(b.dataset.preview)];
        if (!item) return;
        const ticket = ++previewSerial;
        const id = item.version_id;
        const source = await ctx.api("/records/" + encodeURIComponent(id));
        if (
          !valid() ||
          ticket !== previewSerial ||
          item !== last?.items[Number(b.dataset.preview)]
        )
          return;
        if (
          source.project_id !== project ||
          source.kind !== "AssetVersion" ||
          source.version !== item.revision
        )
          throw new Error("素材版本已变更，请重新查找。");
        if (pane.hidden) paneToggle.click();
        selectedItem = item;
        const url = esc(ctx.assetUrl(source));
        const player =
          item.media_type === "image"
            ? `<img src="${url}" alt="${esc(item.name)}">`
            : ["audio", "video"].includes(item.media_type)
              ? `<${item.media_type} controls preload="metadata" src="${url}" aria-label="${esc(item.name)}"></${item.media_type}>`
              : "";
        pane.innerHTML = `<h3 tabindex="-1">${esc(item.name)}</h3><small>素材版本 ${esc(item.revision)}${Number.isFinite(item.seconds) ? " · " + item.seconds.toFixed(2) + " 秒" : ""}</small>${player}<ul class="index-tags">${item.tags.map((t) => `<li>${esc(t.label)}${Array.isArray(t.interval) ? " · " + esc(t.interval.join("–")) + " 秒" : Number.isFinite(t.source_seconds) ? " · " + t.source_seconds.toFixed(2) + " 秒" : ""}${t.review_state === "suggested" ? " · 模型候选" : t.method === "tesseract_lstm" ? " · OCR" : ""}</li>`).join("")}</ul>${item.media_type === "video" ? `<button type="button" class="button secondary small" data-open-source>在剪辑台预览</button>` : ""}`;
        const frameTime =
            item.frame_match?.source_seconds ?? item.tag_match?.source_seconds,
          video = pane.querySelector("video");
        if (video && Number.isFinite(frameTime))
          video.addEventListener(
            "loadedmetadata",
            () => {
              if (Number.isFinite(video.duration))
                video.currentTime = Math.min(frameTime, video.duration);
            },
            { once: true },
          );
        if (
          item.media_type === "video" &&
          Number.isFinite(item.frame_match?.source_seconds) &&
          !["viewer", "reviewer"].includes(ctx.state.health?.actor?.role)
        ) {
          pane.insertAdjacentHTML(
            "beforeend",
            `<details class="director-format"><summary>登记场景或动作标注</summary><form data-reviewed-frame class="form-stack">${select(
              "类型",
              "category",
              [
                ["scene", "场景"],
                ["action", "动作"],
              ],
            )}${field("标签", "label", "", "text", 'required maxlength="300"')}${field("区间起点（秒）", "start", String(frameTime), "number", 'required min="0" step="any"')}${field("区间终点（秒）", "end", "", "number", 'required min="0" step="any"')}${field("核对依据", "evidence", "", "text", 'required maxlength="4000"')}<button type="submit" class="button secondary small">保存人工标注</button><p role="status"></p></form></details>`,
          );
          const review = pane.querySelector("[data-reviewed-frame]");
          review.onsubmit = async (e) => {
            e.preventDefault();
            const btn = review.querySelector("button");
            if (btn.disabled) return;
            btn.disabled = true;
            const values = Object.fromEntries(new FormData(review));
            try {
              await ctx.api("/production/tags", {
                method: "POST",
                body: {
                  ...values,
                  start: Number(values.start),
                  end: Number(values.end),
                  asset_version_id: id,
                  project_id: project,
                  reviewed_frame: {
                    version: item.revision,
                    ...item.frame_match,
                  },
                },
              });
              review.querySelector("[role=status]").textContent =
                "标注已保存，已关联源帧与素材版本。";
            } catch (err) {
              review.querySelector("[role=status]").textContent = err.message;
              btn.disabled = false;
            }
          };
        }
        const link = $("[data-open-source]", pane);
        if (link)
          link.onclick = () => {
            dlg.close();
            ctx.nav(
              "editor/source/" +
                encodeURIComponent(id) +
                "/" +
                item.revision +
                (Number.isFinite(frameTime) ? "/" + frameTime : ""),
            );
          };
        pane.querySelector("h3").focus();
        return;
      }
      if (b.hasAttribute("data-choose")) {
        const item = last?.items[Number(b.dataset.choose)];
        if (!item) return;
        await choose(item);
        if (valid()) dlg.close();
        return;
      }
      if (b.hasAttribute("data-analyze")) {
        analyzing = true;
        stopAnalysis = false;
        b.disabled = true;
        const stop = $("[data-stop-analysis]", dlg);
        stop.hidden = false;
        stop.disabled = false;
        let after = "",
          processed = 0,
          reused = 0,
          frames = 0,
          errors = 0,
          ocrMissing = 0;
        analysisStatus.textContent = "正在读取素材画面…";
        try {
          do {
            const result = await post("/analyze", { limit: 4, after });
            if (!valid()) return;
            processed += result.processed;
            reused += result.reused;
            frames += result.clip_frames;
            errors += result.errors.length;
            ocrMissing += result.ocr_unavailable;
            after = result.next_after;
            analysisStatus.textContent =
              `新分析 ${processed} 份，复用 ${reused} 份，语义索引新增 ${frames} 帧。` +
              (result.clip_status !== "available"
                ? "视觉模型未就绪，已使用实帧颜色、画幅与可用文字识别。"
                : "") +
              (ocrMissing ? ` ${ocrMissing} 份缺少文字识别结果。` : "") +
              (errors ? ` ${errors} 项处理失败。` : "");
          } while (after && !stopAnalysis);
          if (after && stopAnalysis)
            analysisStatus.textContent += " 已停止后续批次。";
          if (valid()) await search();
        } finally {
          analyzing = false;
          stop.hidden = true;
        }
        return;
      }
      if (b.hasAttribute("data-frame-build")) {
        busy = true;
        b.disabled = true;
        status.textContent = "正在本机提取素材画面…";
        const r = await post("/frames/build", { limit: 4, after: frameAfter });
        if (valid()) {
          frameAfter = r.next_after || "";
          b.textContent = frameAfter ? "继续提取下一批" : "提取画面语义索引";
          status.textContent =
            `已索引 ${r.processed} 份素材、${r.frames} 帧，后续 ${r.remaining} 份。` +
            r.errors.map((e) => e.message).join(" ");
        }
        return;
      }
      if (b.hasAttribute("data-embed")) {
        busy = true;
        b.disabled = true;
        const result = await post("/embed", { limit: 128 });
        if (valid())
          status.textContent = `本次编码 ${result.processed} 条，剩余 ${result.remaining} 条 · ${result.space}`;
        return;
      }
      const rebuildActive = b.hasAttribute("data-rebuild-active");
      if (
        !b.hasAttribute("data-build") &&
        !b.hasAttribute("data-switch") &&
        !rebuildActive
      )
        return;
      busy = true;
      b.disabled = true;
      if (b.dataset.build || rebuildActive) {
        const built = await post("/build", {
          mode: b.dataset.build || "shadow",
          expected_revision: history.head.revision,
          request_key: uid(),
        });
        if (rebuildActive && valid()) {
          await post("/switch", {
            snapshot: built.snapshot,
            expected_revision: built.head.revision,
            action: "activate",
          });
        }
      } else {
        const target = history.snapshots[Number(b.dataset.switch)];
        await post("/switch", {
          snapshot: target.id,
          expected_revision: history.head.revision,
          action: target.previously_active ? "rollback" : "activate",
        });
      }
      if (valid()) {
        staleActivation = false;
        await search();
        await loadHistory();
      }
    } catch (err) {
      if (valid()) {
        if (err.code === "INDEX_SOURCE_CHANGED") staleActivation = true;
        status.textContent = err.message;
        toast(err.message, "error");
        await loadHistory().catch(() => {});
      }
    } finally {
      busy = false;
      b.disabled = false;
    }
  }
  try {
    await loadHistory();
    if (valid()) await search();
  } catch (err) {
    if (valid()) status.textContent = err.message;
  }
}

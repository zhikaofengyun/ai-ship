import {
  cohortFrom,
  comparePlacements,
  hypothesisForm,
} from "./placement-comparisons.js";
import { esc, field, formData, modal, select, toast } from "./ui.js";

export function placementPerformance(ctx, onReturn, previous = {}) {
  const project_id = ctx.requireProject();
  const choices = ctx
    .records("Account")
    .map((r) => [r.id, r.data.name || r.id]);
  const seconds = (name, key, value) =>
    field(name, key, value, "number", 'min="0" step="0.001" required');
  const form = modal(
    "比较素材在同一位置的表现",
    '<form class="form-stack" data-form>' +
      select(
        "投放账号",
        "account_id",
        choices,
        previous.account_id || "",
        "required",
      ) +
      select(
        "流量类型",
        "traffic",
        [
          ["organic", "自然"],
          ["paid", "付费"],
          ["mixed", "混合"],
          ["unknown", "未知"],
        ],
        previous.traffic || "organic",
      ) +
      seconds("成片起点（秒）", "start_seconds", previous.start_seconds ?? 0) +
      seconds("成片终点（秒）", "end_seconds", previous.end_seconds ?? 3) +
      field(
        "发布后第几小时开始观察",
        "cohort_after_hours",
        previous.cohort_after_hours ?? 0,
        "number",
        'min="0" max="8760" required',
      ) +
      field(
        "观察时长（小时）",
        "cohort_hours",
        previous.cohort_hours ?? 24,
        "number",
        'min="1" max="8760" required',
      ) +
      '<button type="submit" class="button primary">查看素材表现</button><button class="button secondary" type="button" data-return>返回帖子复盘</button></form>',
    async (f) => {
      const filters = { ...formData(f), project_id };
      let baseline = null;
      async function page(offset) {
        const openDialog = document.querySelector("#dialog");
        const openId = openDialog.dataset.modalId;
        const result = await ctx.api(
          "/production/post-reviews/placement-performance?" +
            new URLSearchParams({
              ...filters,
              offset: String(offset),
              limit: "25",
            }),
        );
        if (
          ctx.state.projectId !== project_id ||
          !openDialog.open ||
          openDialog.dataset.modalId !== openId
        )
          return;
        const list = result.items
          .map(
            (row, index) =>
              '<article class="record-row"><strong>' +
              esc(row.name) +
              "</strong><p>" +
              esc(row.role) +
              " · v" +
              esc(row.source_version) +
              " · 留存 " +
              (100 * row.mean_post_retention).toFixed(1) +
              "%</p><p>" +
              row.posts +
              " 篇帖子 · " +
              row.clip_uses +
              " 处使用 · " +
              row.multi_source_posts +
              " 篇包含其他来源 · 区间起点 " +
              row.entrants +
              " 人次，终点 " +
              row.retained +
              " 人次</p>" +
              "<details><summary>帖子与留存依据</summary>" +
              row.example_evidence
                .map(
                  (x) => "<p>" + esc(x.post) + " · " + esc(x.insight) + "</p>",
                )
                .join("") +
              '</details><button type="button" class="button small secondary" data-learn="' +
              index +
              '">沉淀为选材假设</button><button type="button" class="button small secondary" data-compare="' +
              index +
              '"></button></article>',
          )
          .join("");
        const d = modal(
          "素材位置表现",
          "<p>" +
            esc(filters.start_seconds) +
            " 至 " +
            esc(filters.end_seconds) +
            " 秒 · " +
            result.matching_posts +
            " 篇同口径帖子。每篇按区间末与区间初的人数计算留存，再对帖子等权平均。</p>" +
            (list || "<p>这组条件尚无可比较素材。</p>") +
            "<p>" +
            result.missing_boundary_points +
            " 篇缺少指定时间点，" +
            result.zero_entrants +
            " 篇起点无人，" +
            result.conflicting_latest_posts +
            " 篇最新回填冲突，" +
            result.revised_latest_receipts +
            " 篇回执已改版待复核。同一区间内先后出现或重叠的素材共享观测。</p>" +
            '<div class="dialog-actions"><button class="button secondary" data-back>更改条件</button><button class="button secondary" data-return>返回帖子复盘</button>' +
            (offset
              ? '<button class="button secondary" data-prev>上一页</button>'
              : "") +
            (offset + 25 < result.total
              ? '<button class="button secondary" data-next>下一页</button>'
              : "") +
            "</div>",
          null,
          true,
        );
        for (const b of d.querySelectorAll("[data-learn]")) {
          b.onclick = () => {
            const row = result.items[Number(b.dataset.learn)];
            hypothesisForm(
              ctx,
              "沉淀选材假设",
              row.name +
                " · " +
                row.posts +
                " 篇同口径帖子 · 区间留存 " +
                (100 * row.mean_post_retention).toFixed(1) +
                "%",
              "placement-hypotheses",
              {
                project_id,
                cohort: cohortFrom(filters),
                source: { id: row.source_id, version: row.source_version },
                role: row.role,
                evidence_hash: row.evidence_hash,
              },
              onReturn,
            );
          };
        }
        const compareButtons = [...d.querySelectorAll("[data-compare]")];
        function syncComparison() {
          for (const button of compareButtons) {
            const row = result.items[Number(button.dataset.compare)];
            const same =
              baseline &&
              baseline.source_id === row.source_id &&
              baseline.source_version === row.source_version;
            button.disabled = Boolean(baseline && row.role !== baseline.role);
            button.textContent = !baseline
              ? "设为对照基准"
              : same
                ? "取消对照基准"
                : button.disabled
                  ? "轨道角色不同"
                  : "与「" + baseline.name + "」对照";
            button.onclick = () => {
              if (!baseline || same) {
                baseline = same ? null : row;
                syncComparison();
                return;
              }
              comparePlacements(
                ctx,
                filters,
                baseline,
                row,
                () => page(offset).catch((e) => toast(e.message, "error")),
                onReturn,
              ).catch((e) => toast(e.message, "error"));
            };
          }
        }
        syncComparison();
        d.querySelector("[data-back]").onclick = () =>
          placementPerformance(ctx, onReturn, filters);
        d.querySelector("[data-return]").onclick = onReturn;
        for (const [selector, next] of [
          ["[data-prev]", offset - 25],
          ["[data-next]", offset + 25],
        ]) {
          const b = d.querySelector(selector);
          if (b)
            b.onclick = () =>
              page(next).catch((e) => toast(e.message, "error"));
        }
      }
      await page(0);
    },
  );
  form.querySelector("[data-return]").onclick = onReturn;
}

import { $, esc, field, formData, modal, select, toast } from "./ui.js";

const stateLabels = {
  recorded_response: "已记录的完整回答",
  recorded_receipt: "已记录的平台事件",
  metadata_only: "仅有工具元数据",
  masked: "证据不足或已遮蔽",
};
const checkNames = {
  contains_text: "回答包含指定文字",
  has_json_key: "JSON 含指定顶层字段",
};
const deltaLabels = {
  same_messages: "消息内容与顺序",
  same_system_instructions: "系统指令",
  same_parameters: "推理参数",
  same_model_profile: "模型配置版本",
  same_recorded_content: "回答正文",
  same_evidence_versions: "上下文来源版本",
};

function evidence(item, title) {
  if (!item) return "";
  const meta = JSON.stringify(
    {
      pin: item.pin,
      fingerprints: item.fingerprints,
      receipt: item.receipt,
      inference_parameters: item.inference_parameters,
      dependencies: item.dependencies,
    },
    null,
    2,
  );
  const messageLabel =
    item.transmission === "provider_response_observed"
      ? "实际发送的 "
      : item.transmission === "not_sent"
        ? "未发送的 "
        : "发送待核的 ";
  const checks = Object.entries(item.checks)
    .map(
      ([key, pass]) =>
        "<li>" +
        esc(checkNames[key]) +
        " · " +
        (pass ? "符合" : "不符合") +
        "</li>",
    )
    .join("");
  return (
    "<section><h3>" +
    esc(title) +
    "</h3><p>" +
    esc(item.operation) +
    " · " +
    esc(stateLabels[item.status]) +
    (item.outcome ? " · " + esc(item.outcome) : "") +
    "</p>" +
    (item.issues.length
      ? "<p>" + item.issues.map(esc).join(" · ") + "</p>"
      : "") +
    (item.messages
      ? "<details><summary>" +
        messageLabel +
        item.messages.length +
        " 条消息</summary>" +
        item.messages
          .map(
            (m) =>
              "<p><strong>" +
              esc(m.role) +
              "</strong></p><pre>" +
              esc(m.content) +
              "</pre>",
          )
          .join("") +
        "</details>"
      : "") +
    (item.response
      ? "<details open><summary>冻结回答</summary><pre>" +
        esc(item.response.content) +
        "</pre></details>"
      : "") +
    (checks ? "<ul>" + checks + "</ul>" : "") +
    "<details><summary>版本、指纹与工具回执</summary><pre>" +
    esc(meta) +
    "</pre></details></section>"
  );
}

export async function evidenceReplay(ctx, prior = {}) {
  const project_id = ctx.requireProject(),
    path = "/harness/replay-evidence";
  const list = await ctx.api(
    path +
      "?" +
      new URLSearchParams({
        project_id,
        offset: String(prior.offset || 0),
        q: prior.q || "",
      }),
  );
  if (ctx.state.projectId !== project_id) return;
  const values = list.items.map((r) => [
    r.id + "@" + r.version,
    r.operation + " · " + r.id + " · v" + r.version,
  ]);
  const freeId = (name, label) =>
    field(label, name, prior[name] || "", "text", 'maxlength="180"');
  const d = modal(
    "离线回放与提示词对照",
    '<form data-form class="form-stack">' +
      select("记录", "a", [["", "选择记录"], ...values], prior.a || "") +
      select(
        "对照记录（可选）",
        "b",
        [["", "仅查看一条"], ...values],
        prior.b || "",
      ) +
      "<details><summary>指定历史 ID 与版本</summary>" +
      freeId("left_pin", "记录 ID@版本") +
      freeId("right_pin", "对照 ID@版本") +
      "</details>" +
      freeId("expected_text", "回答须包含的文字（可选）") +
      freeId("json_key", "回答须包含的 JSON 字段（可选）") +
      '<button type="submit" class="button primary">读取冻结证据</button></form>' +
      "<p>可以对照两次已发生调用的消息、模型和回答。此视图只读取保存的响应与回执。</p>" +
      '<form data-search class="form-stack">' +
      field("按记录 ID 筛选", "q", prior.q || "") +
      '<button type="submit" class="button secondary">查找记录</button></form>' +
      (prior.offset
        ? '<button class="button secondary" data-prev>上一页</button>'
        : "") +
      (list.next_offset !== null
        ? '<button class="button secondary" data-next>下一页</button>'
        : ""),
    async (f) => {
      const params = formData(f),
        pin = (v) => {
          const m = /^([a-zA-Z0-9_-]+)@([1-9][0-9]*)$/.exec(v);
          if (!m) throw new Error("请选择记录或输入 ID@版本。");
          return m.slice(1);
        };
      const [id, version] = pin(params.left_pin || params.a),
        query = {
          project_id,
          version,
          expected_text: params.expected_text,
          json_key: params.json_key,
        };
      if (params.right_pin || params.b)
        [query.compare_id, query.compare_version] = pin(
          params.right_pin || params.b,
        );
      const result = await ctx.api(
        path + "/" + encodeURIComponent(id) + "?" + new URLSearchParams(query),
      );
      if (ctx.state.projectId !== project_id) return;
      const comp = result.comparison,
        diff =
          comp?.status === "observed"
            ? "<ul>" +
              Object.entries(deltaLabels)
                .map(
                  ([k, label]) =>
                    "<li>" +
                    esc(label) +
                    " · " +
                    (comp[k] ? "相同" : "不同") +
                    "</li>",
                )
                .join("") +
              "</ul>"
            : comp
              ? "<p>有记录缺少完整回答，无法评定两份输出。</p>"
              : "";
      const out = modal(
        "历史响应回放",
        "<p>本次读取 · 模型重调用 0 次 · 外部写入 0 次</p>" +
          diff +
          evidence(result.left, "选中记录") +
          evidence(result.right, "对照记录") +
          '<button data-back class="button secondary">返回筛选</button>',
        null,
        true,
      );
      $("[data-back]", out).onclick = () =>
        evidenceReplay(ctx, { ...prior, ...params }).catch((e) =>
          toast(e.message, "error"),
        );
    },
    true,
  );
  const go = (opts) =>
    evidenceReplay(ctx, opts).catch((e) => toast(e.message, "error"));
  $("[data-search]", d).onsubmit = (e) => {
    e.preventDefault();
    go({ q: formData(e.currentTarget).q });
  };
  if ($("[data-prev]", d))
    $("[data-prev]", d).onclick = () =>
      go({ ...prior, offset: Math.max(0, prior.offset - 50) });
  if ($("[data-next]", d))
    $("[data-next]", d).onclick = () =>
      go({ ...prior, offset: list.next_offset });
}

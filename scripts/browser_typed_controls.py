"""Focused positive/negative control tests and hostile imported-identifier DOM checks.

The first checks mount explicit synthetic renderer fixtures in a real browser;
the final checks use actual isolated API records and workflow execution.
These checks do not substitute for complete product regression rounds.
"""

import argparse, importlib.util, json, tempfile, sys, os, shutil, subprocess, hashlib, re
from datetime import datetime, timezone
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(
    description="Focused real-browser controls and hostile-import DOM checks; not a full regression round"
)
parser.add_argument("--output", default="evidence/development-typed")
args = parser.parse_args()
spec = importlib.util.spec_from_file_location(
    "browser_regression", ROOT / "scripts/browser_regression.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
out = Path(args.output).resolve()
out.mkdir(parents=True, exist_ok=True)
report = {
    "scope": "Focused development tests; not a full regression round",
    "started_at": datetime.now(timezone.utc).isoformat(),
    "checks": [],
    "errors": [],
    "limitations": [
        "Initial DOM component checks use explicit synthetic renderer contexts, not provider/API execution",
        "Final workflow and media checks use real isolated API records, the manual-script handler and FFmpeg",
        "No external models, platform publication, Safari or hardware tests",
    ],
}
try:
    with (
        tempfile.TemporaryDirectory() as td,
        m.server(Path(td), out) as base,
        sync_playwright() as pw,
    ):
        browser = pw.chromium.launch(
            executable_path=os.getenv("AI_SHIP_CHROMIUM")
            or shutil.which("chromium-headless-shell")
            or shutil.which("chromium"),
            headless=True,
            chromium_sandbox=True,
        )
        report["browser_version"] = browser.version
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.on("pageerror", lambda e: report["errors"].append(str(e)))
        page.goto(base)
        expect(page.locator("#main h1")).to_have_text("项目")
        result = page.evaluate("""async()=>{
   const c=await import('/controls.js'),u=await import('/ui.js'),ex=await import('/execution-controls.js');
   const area=document.createElement('div');document.body.append(area);const checks=[];
   function assert(value,label){if(!value)throw new Error(label);checks.push(label)}
   const render=(control,value,ctx={})=>{area.innerHTML=c.renderControls([control]);c.mountControls(area,[control],value===undefined?{}:{[control.key]:value},ctx);};
   const input=(selector,value)=>{const e=area.querySelector(selector);e.value=value;e.dispatchEvent(new Event('input',{bubbles:true}));};
   const reference={key:'ref',type:'reference',label:'版本引用'},old={id:'a',kind:'Asset',version:1},records=[{id:'a',kind:'Asset',version:3,data:{name:'已升级素材'}}];
   render(reference,old,{records});assert(c.readControls(area).ref.version===1,'reference old revision remains pinned without user input');area.querySelector('[data-upgrade-ref]').click();assert(c.readControls(area).ref.version===3,'reference upgrades only through explicit user action');
   render({key:'weights',type:'weights',label:'权重'});area.querySelector('[data-add-row]').click();input('[data-col="名称"]','质量');let rejected=false;try{c.readControls(area)}catch{rejected=true}assert(rejected,'empty weight is rejected rather than coerced to zero');input('[data-col="权重"]','0');assert(c.readControls(area).weights['质量']===0,'explicit zero weight is valid');area.querySelector('[data-add-row]').click();input('[data-row="1"][data-col="名称"]','质量');input('[data-row="1"][data-col="权重"]','1');rejected=false;try{c.readControls(area)}catch{rejected=true}assert(rejected,'duplicate weights are rejected rather than overwritten');
   render({key:'calendar',type:'timeRange',label:'有效日期',timeDomain:'business_validity'},{start:'2026-11-24',end:'2026-11-30',timezone:'Asia/Shanghai',boundary:'[start,end]',time_domain:'business_validity'});input('[data-calendar="end"]','2026-12-01');const cal=c.readControls(area).calendar;assert(cal.start==='2026-11-24'&&cal.end==='2026-12-01'&&cal.time_domain==='business_validity'&&!('ticks'in cal),'business validity retains calendar, timezone and boundary semantics');
   render({key:'duration',type:'duration',label:'预留期限',timeDomain:'business_duration'});assert(!area.querySelector('option[value=frames]'),'business durations never offer media frames');
   render({key:'scale',type:'number',label:'轻微放大',range:{min:1.011,max:1.022}});input('input[type=number]','1.03');rejected=false;try{c.readControls(area)}catch{rejected=true}assert(rejected,'source range min/max is enforced');
   render({key:'point',type:'timecode',label:'音乐起点',timeDomain:'audio_point'},{ticks:48000,timebase_num:1,timebase_den:48000,time_domain:'audio_point'});input('[data-point=ticks]','96000');assert(c.readControls(area).point.ticks===96000&&c.readControls(area).point.timebase_den===48000,'audio time point retains rational time base');
   let calls=0;area.innerHTML='<button data-action="test">x</button>';u.onAction(area,{test:()=>calls+=100});u.onAction(area,{test:()=>calls++});area.querySelector('button').click();assert(calls===1,'action delegation replaces previous listener');
   const capabilities=(await (await fetch('/api/v1/execution/capabilities')).json()).data;let dirty=0;const control=ex.mountExecutionControls(area,capabilities.handlers['I07-R2'],{operation_args:{recipe:{scale:1.016}},input_bindings:{asset_id:{record_id:'a',version:1,value:'id'}}},{records,upstream:[],capabilities:capabilities.handlers,feature:()=>null,onchange:()=>dirty++});assert(control.collect().input_bindings.asset_id.version===1,'executor binding preserves pinned input version');const binding=area.querySelector('[data-execution-binding]');binding.value='r:a';binding.dispatchEvent(new Event('change',{bubbles:true}));assert(control.collect().input_bindings.asset_id.version===3,'executor binding has explicit version replacement');assert(control.collect().operation_args.recipe.scale===1.016,'executor typed form preserves untouched real recipe');
   u.toast('same');u.toast('same');u.toast('other');u.toast('last');assert(document.querySelectorAll('.toast').length===2,'toasts deduplicate and are limited to two');
   const pending=u.confirmAction('取消检查','只验证关闭按钮不会悬挂Promise');document.querySelector('#dialog .dialog-close-form button').click();assert(await pending===false,'confirmation close button resolves cancellation');
   const focusDialog=u.modal('焦点竞争检查','<input name=first aria-label=first><textarea name=second aria-label=second></textarea>');const secondField=focusDialog.querySelector('[name=second]');secondField.focus();await new Promise(resolve=>setTimeout(resolve,70));assert(document.activeElement===secondField,'opening a dialog never steals focus after the user moves to another field');focusDialog.close();
   area.remove();
   const catalog=(await (await fetch('/api/v1/catalog')).json()).data;
   const hostile='item\"><img data-injected=\"yes\" src=\"/missing\">';
   const canvas=document.createElement('div');canvas.style.width='1000px';document.body.append(canvas);
   const workflow={id:'workflow-hostile',kind:'Workflow',version:1,data:{name:'Hostile DOM fixture',nodes:[{id:hostile,feature_id:'V01',route_id:'V01-R3',parameters:{},operation_args:{data:{text:'Explicit synthetic fixture'}},input_bindings:{}}],edges:[],max_steps:3,max_attempts:1,budget:0}};
   const view={id:'view-hostile',kind:'View',data:{name:'流程画布',operation_scope:{workflow_id:workflow.id},layout:{positions:{[hostile]:{x:'0;position:fixed',y:100}},zoom:'1)\"><img data-injected=\"yes\">'}}};
   const ctx={state:{projectId:'dom-hostile-workflow',catalog,records:[workflow,view],executionCapabilities:capabilities,cleanup:[]},records:kind=>[workflow,view].filter(r=>r.kind===kind),bind:handlers=>u.onAction(canvas,handlers),api:async path=>(await (await fetch('/api/v1'+path)).json()).data,requireProject:()=>true,configFor:()=>({parameters:{}}),ref:r=>({id:r.id,version:r.version,kind:r.kind}),nav:()=>{},reloadData:async()=>{}};
   await (await import('/workflow.js')).mountWorkflow(canvas,ctx);canvas.querySelector('.workflow-node').click();assert(canvas.querySelectorAll('[data-injected]').length===0,'hostile workflow node ids and layout strings remain inert DOM data');assert(canvas.querySelector('.workflow-node').dataset.node===hostile,'workflow preserves escaped opaque node id');
   const reserved=structuredClone(workflow);reserved.id='workflow-reserved';reserved.data.nodes[0].id='__proto__';ctx.state.projectId='dom-reserved-id';ctx.state.records=[reserved];ctx.records=kind=>kind==='Workflow'?[reserved]:[];let badId=false;try{await (await import('/workflow.js')).mountWorkflow(canvas,ctx)}catch{badId=true}assert(badId,'reserved prototype node id is rejected before layout access');
   const opaque=structuredClone(workflow);opaque.id='workflow-opaque';opaque.data.nodes[0].id='toString';ctx.state.projectId='dom-opaque-id';ctx.state.records=[opaque];ctx.records=kind=>kind==='Workflow'?[opaque]:[];await (await import('/workflow.js')).mountWorkflow(canvas,ctx);assert(canvas.querySelector('.workflow-node').style.left==='70px','opaque node id gets an own layout instead of inherited prototype property');
   const timeline={id:'timeline-hostile',kind:'Timeline',version:1,data:{name:'Hostile timeline fixture',format:{width:1080,height:1920,fps:30},clips:[{id:hostile,asset_id:'missing-test-asset',source_in:0,source_out:1,timeline_start:0,track:0,opacity:1,volume:1,fit:'cover;position:fixed',keyframes:{opacity:[{time:0,value:'<img data-injected=\"yes\">'}]}}],captions:[],audio_tracks:[]}};
   canvas.innerHTML='';ctx.state.projectId='dom-hostile-timeline';ctx.state.records=[timeline];ctx.records=kind=>kind==='Timeline'?[timeline]:[];ctx.assetUrl=r=>'/api/v1/media/'+encodeURIComponent(r.id)+'/file';
   await (await import('/editor.js')).mountEditor(canvas,ctx);canvas.querySelector('[data-clip]').click();assert(canvas.querySelectorAll('[data-injected]').length===0,'hostile timeline clip id and keyframe values remain inert DOM data');assert(canvas.querySelector('[data-clip]').dataset.clip===hostile,'timeline preserves escaped opaque clip id');ctx.state.cleanup.forEach(fn=>fn());canvas.remove();
   return checks;
  }""")
        report["checks"] += result
        with httpx.Client(base_url=base + "/api/v1", trust_env=False, timeout=30) as client:
            pr = client.post(
                "/projects",
                json={
                    "name": "控件联调 · 合成项目",
                    "goals": "只用于隔离开发检查",
                    "language": "zh-CN",
                    "market": "test",
                    "content_mode": "custom",
                    "budget": 0,
                    "duration": 30,
                    "width": 1080,
                    "height": 1920,
                },
            )
            pr.raise_for_status()
            pid = pr.json()["data"]["id"]
            flow = {
                "name": "人工脚本字段与布局",
                "nodes": [
                    {
                        "id": "manual-script",
                        "feature_id": "V01",
                        "route_id": "V01-R3",
                        "parameters": {},
                        "operation_args": {"data": {"text": "原始脚本"}},
                        "input_bindings": {},
                    }
                ],
                "edges": [],
                "max_steps": 3,
                "max_attempts": 2,
                "budget": 0,
            }
            fr = client.post(
                "/records",
                json={
                    "kind": "Workflow",
                    "project_id": pid,
                    "data": flow,
                    "reason": "focused synthetic browser fixture",
                },
            )
            fr.raise_for_status()
            fid = fr.json()["data"]["id"]
            page.goto(base + "/#workflow")
            page.reload()
            expect(page.locator(".workflow-node")).to_have_count(1)
            page.locator(".workflow-node").click()
            expect(page.locator("#node-execution textarea")).to_have_value("原始脚本")
            page.locator("#node-execution textarea").fill("人工明确修订的脚本")
            page.locator("[data-action=save-workflow]").click()
            expect(page.locator("#workflow-status")).to_contain_text("已保存")
            first = client.get("/records/" + fid).json()["data"]
            assert (
                first["data"]["nodes"][0]["operation_args"]["data"]["text"] == "人工明确修订的脚本"
            )
            report["checks"].append(
                "actual workflow operation form persists typed manual script through API"
            )
            box = page.locator(".workflow-node").bounding_box()
            page.mouse.move(box["x"] + 80, box["y"] + 30)
            page.mouse.down()
            page.mouse.move(box["x"] + 160, box["y"] + 100, steps=8)
            page.mouse.up()
            page.locator("[data-action=save-workflow]").click()
            expect(page.locator("#workflow-status")).to_contain_text("已保存")
            second = client.get("/records/" + fid).json()["data"]
            assert first["version"] == second["version"], (first["version"], second["version"])
            report["checks"].append(
                "layout-only node dragging persists View without changing Workflow revision"
            )
            page.locator("[data-action=run-workflow]").click()
            expect(page.locator("dialog[open] h2")).to_have_text("工作流运行请求", timeout=30000)
            runs = client.get("/records", params={"project_id": pid, "kind": "Run"}).json()["data"]
            assert any(
                r["data"].get("status") == "completed"
                and r["data"].get("execution_kind") == "local_dag"
                for r in runs
            )
            report["checks"].append(
                "actual UI starts manual script handler and receives completed real output"
            )
            page.locator("dialog[open]").get_by_role("button", name="关闭", exact=True).click()
            page.screenshot(path=str(out / "workflow-typed.png"))
            fixture = Path(td) / "typed-synthetic-media.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=320x180:rate=24",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=660:sample_rate=48000",
                    "-t",
                    "2",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-y",
                    str(fixture),
                ],
                check=True,
                timeout=30,
            )
            page.goto(base + "/#assets")
            expect(page.locator("#main h1")).to_have_text("素材库")
            page.locator("#media-input").set_input_files(str(fixture))
            expect(page.locator(".asset-card")).to_have_count(1, timeout=15000)
            original = client.get("/records", params={"project_id": pid, "kind": "Asset"}).json()[
                "data"
            ][0]
            original_versions = client.get(
                "/records", params={"project_id": pid, "kind": "AssetVersion"}
            ).json()["data"]
            assert (
                len(original_versions) == 1
                and original_versions[0]["data"]["variant"] == "original"
            )
            report["checks"].append(
                "actual media library shows one original without duplicate original AssetVersion card"
            )
            page.locator(".asset-card").click()
            page.locator("dialog[open] [data-action=transform]").click()
            page.locator("dialog[open] [name=trim_start]").fill("0.4")
            page.locator("dialog[open] [name=trim_end]").fill("1.4")
            page.locator("dialog[open] [name=brightness]").fill("0.1")
            page.locator("dialog[open] form[data-form] button[type=submit]").click()
            expect(page.locator("dialog[open]")).to_have_count(0, timeout=30000)
            expect(page.locator(".asset-card")).to_have_count(2)
            versions = client.get(
                "/records", params={"project_id": pid, "kind": "AssetVersion"}
            ).json()["data"]
            derived = next(v for v in versions if v["data"]["variant"] == "derivative")
            derivative_file = client.get("/media/" + derived["id"] + "/file")
            derivative_file.raise_for_status()
            assert len(derivative_file.content) > 1000
            report["checks"].append(
                "real UI trim and transform produces a visible, downloadable derivative AssetVersion"
            )
            page.locator('.asset-card[data-id="' + derived["id"] + '"]').click()
            expect(page.locator("dialog[open] .media-detail h3")).to_have_text("衍生版本")
            expect(page.locator("dialog[open] video")).to_have_attribute(
                "src", re.compile(re.escape(derived["id"]))
            )
            page.wait_for_function(
                "id => {const v=document.querySelector('dialog[open] video');return v && v.readyState>=1 && v.currentSrc.includes(id) && v.duration<1.2}",
                arg=derived["id"],
            )
            page.locator("dialog[open] [data-action=edit]").click()
            expect(page.locator(".timeline-clip[data-clip]")).to_have_count(1)
            expect(page.locator("#preview-stage video")).to_have_attribute(
                "src", re.compile(re.escape(derived["id"]))
            )
            assert (
                abs(
                    float(page.locator("[data-property=source_out]").input_value())
                    - derived["data"]["probe"]["duration"]
                )
                < 0.01
            )
            report["checks"].append(
                "derivative detail and timeline preview use selected version bytes and output duration"
            )
            page.locator("[data-action=format]").click()
            page.locator("dialog[open] [name=width]").fill("320")
            page.locator("dialog[open] [name=height]").fill("180")
            page.locator("dialog[open] [name=fps]").select_option("24")
            page.locator("dialog[open] form[data-form] button[type=submit]").click()
            expect(page.locator("dialog[open]")).to_have_count(0)
            page.locator("#clip-volume").evaluate(
                "el=>{el.value='0';el.dispatchEvent(new Event('input',{bubbles:true}))}"
            )
            page.locator("[data-action=save-timeline]").click()
            expect(page.locator("#edit-status")).to_contain_text("已保存")
            timeline = client.get(
                "/records", params={"project_id": pid, "kind": "Timeline"}
            ).json()["data"][0]
            clip = timeline["data"]["clips"][0]
            assert (
                clip["asset_id"] == original["id"]
                and clip["asset_version_id"] == derived["id"]
                and clip["volume"] == 0
            )
            assert any(
                r["id"] == derived["id"] and r["version"] == derived["version"]
                for r in timeline["refs"]
            )
            report["checks"].append(
                "timeline save pins parent Asset plus selected derivative id and exact provenance reference"
            )
            page.goto(base + "/#assets")
            expect(page.locator(".asset-card")).to_have_count(2)
            page.locator('.asset-card[data-id="' + derived["id"] + '"]').click()
            page.locator("dialog[open] [data-action=extract]").click()
            expect(page.locator("dialog[open] audio")).to_have_count(1, timeout=30000)
            audio = client.get("/records", params={"project_id": pid, "kind": "Audio"}).json()[
                "data"
            ][0]
            assert audio["data"]["asset_version_id"] == derived["id"]
            expect(page.locator("dialog[open] audio")).to_have_attribute(
                "src", re.compile(re.escape(audio["id"]))
            )
            page.wait_for_function(
                "id=>{const a=document.querySelector('dialog[open] audio');return a&&a.readyState>=1&&a.currentSrc.includes(id)&&a.duration<1.2}",
                arg=audio["id"],
            )
            page.locator("dialog[open]").get_by_role("button", name="关闭", exact=True).click()
            expect(page.locator(".asset-card")).to_have_count(3)
            expect(page.get_by_text("3 件素材", exact=True)).to_be_visible()
            while page.locator(".toast button").count():
                page.locator(".toast button").first.click()
            page.screenshot(path=str(out / "media-library-derived-audio.png"))
            page.locator("[data-action=asset-filter][data-value=audio]").click()
            expect(page.locator(".asset-card")).to_have_count(1)
            expect(page.locator(".asset-card")).to_have_attribute("data-id", audio["id"])
            report["checks"].append(
                "real extracted Audio immediately appears in library and audio filter with playable bytes"
            )
            page.locator(".asset-card").click()
            page.locator("dialog[open] [data-action=edit]").click()
            expect(page.locator(".timeline-clip[data-clip]")).to_have_count(2)
            expect(page.locator('.timeline-clip[data-track="2"]')).to_have_count(1)
            expect(page.locator("#preview-stage audio")).to_have_attribute(
                "src", re.compile(re.escape(audio["id"]))
            )
            expect(page.locator("#preview-stage video")).to_have_attribute(
                "src", re.compile(re.escape(derived["id"]))
            )
            page.locator("[data-action=save-timeline]").click()
            expect(page.locator("#edit-status")).to_contain_text("已保存")
            timeline = client.get("/records/" + timeline["id"]).json()["data"]
            assert (
                len(timeline["data"]["clips"]) == 1 and len(timeline["data"]["audio_tracks"]) == 1
            )
            audio_track = timeline["data"]["audio_tracks"][0]
            assert (
                audio_track["asset_id"] == audio["id"]
                and not audio_track.get("asset_version_id")
                and audio_track["track"] == 2
            )
            assert abs(audio_track["source_out"] - audio["data"]["probe"]["duration"]) < 0.01
            assert any(
                r["id"] == audio["id"] and r["version"] == audio["version"]
                for r in timeline["refs"]
            )
            report["checks"].append(
                "Audio enters an independent audio track with exact source reference and never becomes a video clip"
            )
            while page.locator(".toast button").count():
                page.locator(".toast button").first.click()
            page.screenshot(path=str(out / "editor-derived-audio.png"))
            page.locator("[data-action=render-timeline]").click()
            page.locator("dialog[open]").get_by_role(
                "button", name="锁定并渲染", exact=True
            ).click()
            expect(page.locator("dialog[open] h2")).to_have_text("成片已生成", timeout=60000)
            artifact = client.get(
                "/records", params={"project_id": pid, "kind": "Artifact"}
            ).json()["data"][0]
            assert artifact["data"]["publish_ready"] is False
            rendered = client.get("/media/artifacts/" + artifact["id"] + "/file")
            rendered.raise_for_status()
            render_path = out / "media-chain-render.mp4"
            render_path.write_bytes(rendered.content)
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(render_path), "-f", "null", "-"],
                check=True,
                capture_output=True,
                timeout=30,
            )
            measure = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-i",
                    str(render_path),
                    "-vn",
                    "-af",
                    "volumedetect",
                    "-f",
                    "null",
                    "-",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            mean = re.search(r"mean_volume: (-?[\d.]+) dB", measure.stderr)
            assert mean and float(mean.group(1)) > -60, measure.stderr
            report["checks"].append(
                "derived video plus separate extracted Audio locks, renders, fully decodes and has audible audio with clip audio muted"
            )
            report["media_chain"] = {
                "original_sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                "derivative_id": derived["id"],
                "derivative_sha256": hashlib.sha256(derivative_file.content).hexdigest(),
                "audio_id": audio["id"],
                "timeline_id": timeline["id"],
                "artifact_id": artifact["id"],
                "render_sha256": hashlib.sha256(rendered.content).hexdigest(),
                "render_bytes": len(rendered.content),
                "mean_volume_db": float(mean.group(1)),
                "clip_volume": 0,
                "audio_track_volume": audio_track["volume"],
                "publish_ready": False,
            }
        browser.close()
    report["passed"] = not report["errors"]
except Exception as exc:
    report["passed"] = False
    report["failure"] = repr(exc)
finally:
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False))
if not report["passed"]:
    sys.exit(1)

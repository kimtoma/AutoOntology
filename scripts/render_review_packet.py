# SPDX-License-Identifier: MIT
"""Render an offline local review entry from verified captures; never read credentials."""

import argparse
import base64
import hashlib
import html
import json
from datetime import datetime
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--runtime", type=Path, required=True)
args = parser.parse_args()
runtime = args.runtime.absolute()
review = runtime / "review-20261031"
prepared = json.loads((review / "prepared.json").read_text())
verified = json.loads((review / "verification.json").read_text())
downloads = json.loads((review / "downloads-verification.json").read_text())
manifest = json.loads((review / "captures/manifest.json").read_text())
assert (
    verified["login_passed"] and verified["graph_rendered"] and verified["published_r8_unchanged"]
)
assert len(downloads["originals_sha256_matched"]) == 6 and len(downloads["export_downloads"]) >= 3
E = html.escape
base = "http://127.0.0.1:7840/ui/"
case = prepared["case_id"]
candidate = prepared["controlled_fictitious_candidate"]
code = prepared["source_commit"]
root = runtime.parents[1]
app = review / "app"
credential = review / "runtime/workspace/security/reviewer.json"
prefix = f"cd {app}\nAUTOONTOLOGY_ENV_FILE={review / 'runtime/local.env'} \\\nPYTHONPATH={app}:{root / 'vendor/auto-ontology'} \\\n{root / '.venv/bin/python'} -m autoontology --credential-file {credential} "
steps = [
    (
        "운영 현황",
        "현재 운영 지식과 새 검토 업무",
        base + "?lang=ko",
        "운영 r8의 118개 개념·6개 문서·487개 근거를 확인하고, 가상 후보 r9와 확인 대기 업무를 함께 봅니다.",
        "ops cases",
        "01-review-operations.png",
    ),
    (
        "지식·근거",
        "그래프에서 실제 보존 원문으로",
        base + "graph?lang=ko",
        "현재 운영본의 개념·관계와 근거를 탐색합니다. 보존 원문 6개는 실제 다운로드 바이트의 SHA-256이 일치했습니다.",
        "ops documents --revision 8",
        "02-operational-graph.png",
    ),
    (
        "사람의 판단",
        "가상 규정의 정확한 확인 묶음",
        base + "review?tab=cases&case=" + case + "&lang=ko",
        "담당자·기한·시행일·금액·승인자·원문·필수 평가를 확인합니다. 가상 제안은 확인 대기 상태이며, 확인 뒤 평가와 게시로 이어집니다.",
        "ops case " + case,
        "03-case-evidence-exact-packet.png",
    ),
    (
        "평가·운영",
        "질문과 규칙 검사 버전을 확인",
        base + "history?lang=ko",
        "가상 규정의 대표 질문·알려진 인용·명시적 모델 필드 기준을 봅니다. 준비 과정에서는 모델 평가나 운영 게시를 대신 실행하지 않았습니다.",
        "ops quality",
        "04-versioned-evaluation.png",
    ),
    (
        "지속 수집",
        "사람과 에이전트가 자기 배정만 처리",
        base + "sources?lang=ko#collection-ui",
        "가상 문서와 별도 PostgreSQL의 실제 관찰을 봅니다. 예약·작업자 실행·재시도·담당자 알림을 구분하고, 현재 범위와 만료를 확인합니다.",
        "ops collection",
        "05-human-agent-observation.png",
    ),
    (
        "질문·재사용",
        "운영본 질문과 보존된 산출물",
        base + "playground?lang=ko",
        "새 질문은 직접 요청할 때 설정된 Codex/Ollama를 사용합니다. 미승인 가상 규정은 기본 운영본 답변에 포함되지 않습니다. 기존 산출물 3개 다운로드도 확인했습니다.",
        "ops publications",
        "06-preserved-downloads.png",
    ),
    (
        "다음 개선",
        "결정 기록에서 조직 지식 OS로",
        "../autoontology-statechart-guide.html#current-status",
        "검토·업무 결정 이력은 자동 기록됩니다. 이를 학습·평가해 정책 범위에서 실행하는 agent loop, 소비 계약과 업무 액션은 다음 개발입니다.",
        "ops rules",
        None,
    ),
]
panels = []
flow = []
for i, (label, title, url, description, command, image) in enumerate(steps):
    flow.append(
        f'<button class="flow-step" data-step="{i}" aria-controls="step-{i}" aria-selected="{str(i == 0).lower()}"><span>{i + 1:02}</span><strong>{E(label)}</strong></button>'
    )
    screenshot = ""
    if image:
        raw = (review / "captures" / image).read_bytes()
        record = next(v for v in manifest["captures"] if v["file"] == image)
        assert hashlib.sha256(raw).hexdigest() == record["sha256"]
        encoded = base64.b64encode(raw).decode()
        screenshot = f'<figure><button class="image-button" data-enlarge="shot-{i}" aria-label="{E(title)} 화면 확대"><img id="shot-{i}" src="data:image/png;base64,{encoded}" alt="{E(title)} 실제 라이트모드 화면" loading="lazy"></button><figcaption>{E(record["assertion"])}</figcaption></figure>'
    full = prefix + command
    panels.append(
        f'<article id="step-{i}" class="step-panel" {"hidden" if i else ""}><div class="panel-heading"><div><p class="eyebrow">REVIEW {i + 1:02}</p><h2>{E(title)}</h2></div><a class="primary" href="{E(url)}" target="_blank" rel="noopener">실제 화면 열기 ↗</a></div><p class="description">{E(description)}</p>{screenshot}<details class="cli"><summary>같은 내용을 CLI로 확인</summary><pre><code>{E(full)}</code></pre><button data-copy="{i}">명령 복사</button><textarea id="command-{i}" hidden>{E(full)}</textarea></details><label class="review-check"><input type="checkbox" data-check="{i}"> 이 흐름을 확인했습니다</label></article>'
    )
status_rows = [
    (
        "초기 구축·검토·게시·복원·조회",
        "핵심 구현·범위 검증",
        "실제 모델/DB를 사용한 이전 수용 시험과 최신 회귀 검증. 모든 환경·권한·실패 조합의 전면 보증은 미완료.",
    ),
    (
        "사람·역할·도메인·근거·저장 결과",
        "구현·범위 검증",
        "정확한 개인 확인과 철회. 초기 소유자 복구·전면 권한 감사·cross-resource journal은 남음.",
    ),
    (
        "업무·규칙·평가·피드백",
        "구현·범위 검증",
        "담당자·시행일·금액·권한·평가 버전·운영 결과·재개. 개념 identity/소비 영향·의미적 자동 판단은 남음.",
    ),
    (
        "지속 수집·영속 큐·담당자 알림",
        "1차 구현·범위 검증",
        "실제 폴더/PG 관찰과 실패 복구. 준비 빌드 복구는 제어된 builder 시험; 관리형 worker 수명주기·문서 저장소 pilot은 남음.",
    ),
    (
        "UI·CLI의 대기",
        "로컬 실측",
        "첫 데이터 269.6→182.1ms, 제어한 2초 지연 2150.4→133.6ms, native 조회 193.0→79.4ms. 실제 조직 절감 측정은 아님.",
    ),
    (
        "결정 학습·정책 기반 자율성",
        "미완료",
        "실제 결정 이력을 학습·평가에 쓰고, 만료·한도·철회·복구를 가진 정책으로 실행하며 사람에게 예외를 인계할 것.",
    ),
    (
        "도메인 카탈로그·소비 계약·업무 액션",
        "미완료",
        "소비자/버전/호환성/영향과 별도 업무 권한·확인·중복 방지·보상 계약을 구현할 것.",
    ),
]
rows = "".join(
    "<tr><th>"
    + E(a)
    + '</th><td><span class="status '
    + ("pending" if b == "미완료" else "")
    + '">'
    + E(b)
    + "</span></td><td>"
    + E(c)
    + "</td></tr>"
    for a, b, c in status_rows
)
control = f"{root / '.venv/bin/python'} {review / 'control.py'}"
css = """*{box-sizing:border-box}html{color-scheme:light;scroll-padding-top:85px}body{margin:0;color:#18181b;background:#fff;font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;word-break:keep-all;overflow-wrap:anywhere}button,input{font:inherit}a{color:inherit;text-underline-offset:4px}button,a{outline-offset:4px}button:focus-visible,a:focus-visible{outline:2px solid #1d5e9f}.top{height:64px;position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:16px;padding:0 24px;background:#fff;border-bottom:1px solid #e5e5e5}.top strong{font-size:14px;font-weight:600}.top span{margin-left:auto;font-size:11px;color:#777}.top button{border:0;background:none;padding:8px}.shell{display:grid;grid-template-columns:214px minmax(0,1fr)}nav{height:calc(100vh - 64px);position:sticky;top:64px;border-right:1px solid #eee;padding:24px 14px;overflow:auto}nav a{display:block;padding:10px 12px;font-size:12px;text-decoration:none;color:#555;border-radius:4px}nav a:hover{background:#f6f6f6}.nav-group{padding:18px 12px 7px;font-size:10px;color:#999;letter-spacing:1px}main{max-width:1290px;min-width:0;width:100%;margin:auto;padding:34px 38px 70px}.eyebrow{font-size:10px;color:#777;letter-spacing:1px;margin:0 0 9px}h1{font-size:29px;letter-spacing:-1px;font-weight:550;line-height:1.4;margin:8px 0 14px}h2{font-size:21px;font-weight:550;letter-spacing:-.4px;line-height:1.4;margin:7px 0 14px}h3{font-size:14px;font-weight:600}.intro{max-width:850px;color:#666}.meta-row{display:flex;flex-wrap:wrap;gap:22px;padding:16px 0;border-block:1px solid #e5e5e5;margin:26px 0;font-size:12px;color:#666}.meta-row b{color:#111;font-weight:550}.primary{display:inline-block;padding:9px 14px;border:1px solid #171717;border-radius:5px;background:#171717;color:white;text-decoration:none;font-size:12px;white-space:nowrap}.secondary{display:inline-block;padding:9px 13px;border:1px solid #ddd;border-radius:5px;text-decoration:none;font-size:12px}.actions{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0}.flow{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border:1px solid #e5e5e5;border-radius:5px;overflow:hidden;margin-top:24px}.flow-step{background:#fff;text-align:left;border:0;border-right:1px solid #eee;padding:15px 12px;cursor:pointer}.flow-step:last-child{border-right:0}.flow-step span{display:block;font-size:10px;color:#999}.flow-step strong{display:block;font-size:12px;font-weight:500;margin-top:5px}.flow-step[aria-selected=true]{background:#f4f4f5;box-shadow:inset 0 -2px #171717}.flow-step:hover{background:#fafafa}.step-panel{padding:26px 0}.panel-heading{display:flex;align-items:start;justify-content:space-between;gap:15px;flex-wrap:wrap}.description{max-width:900px;color:#555;font-size:13px}.note{border-left:2px solid #bcbcbc;background:#fafafa;padding:13px 16px;font-size:12px;line-height:1.8;color:#666}figure{margin:22px 0}.image-button{display:block;padding:0;width:100%;border:1px solid #e2e2e2;border-radius:5px;overflow:hidden;background:#fff;cursor:zoom-in}.image-button img{display:block;width:100%;height:auto}figcaption{font-size:11px;color:#777;margin-top:9px}.review-check{display:flex;align-items:center;gap:9px;margin:20px 0;font-size:12px}.cli{border-block:1px solid #eee;padding:13px 0;font-size:12px}.cli summary{cursor:pointer}.cli button{padding:5px 11px;background:#fff;border:1px solid #ddd;border-radius:4px;font-size:11px}.table-wrap{overflow:auto}table{width:100%;border-collapse:collapse;font-size:12px;line-height:1.8}th,td{border-bottom:1px solid #e5e5e5;padding:14px 11px;text-align:left;vertical-align:top}th{font-weight:500;min-width:170px}td:nth-child(2){min-width:145px}.status{font-size:10px;color:#446451;border:1px solid #d6e2da;background:#f8fbf9;border-radius:4px;padding:3px 7px;white-space:nowrap}.status.pending{color:#886d3e;border-color:#e6dcc8;background:#fdfaf4}section.block{padding-top:32px;margin-top:30px;border-top:1px solid #ddd}.discussion{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border:1px solid #e5e5e5;border-radius:5px}.discussion>div{padding:20px;border-right:1px solid #e5e5e5}.discussion>div:last-child{border:0}.discussion p{font-size:12px;color:#666}.links{display:flex;flex-wrap:wrap;gap:16px;font-size:12px;margin:15px 0}pre{white-space:pre-wrap;overflow:auto;font:11px/1.7 ui-monospace,monospace;background:#fafafa;padding:15px;border:1px solid #eee;border-radius:4px}footer{margin-top:40px;border-top:1px solid #eee;padding-top:16px;color:#888;font-size:11px}.collapsed .shell{grid-template-columns:0 minmax(0,1fr)}.collapsed nav{visibility:hidden;padding:0}.progress{font-size:11px;color:#777}dialog{max-width:95vw;max-height:95vh;border:1px solid #ccc;padding:10px;border-radius:5px}dialog::backdrop{background:#0008}dialog img{display:block;max-width:90vw;max-height:88vh;object-fit:contain}dialog button{float:right;padding:5px 10px;border:0;background:#fff}body:has(dialog[open]){overflow:hidden}[hidden]{display:none!important}@media(max-width:1000px){.flow{grid-template-columns:repeat(4,minmax(0,1fr))}.flow-step{border-bottom:1px solid #eee}.discussion{grid-template-columns:1fr}.discussion>div{border-right:0;border-bottom:1px solid #eee}}@media(max-width:700px){.shell{display:block}nav{display:none;position:fixed;top:64px;left:0;width:250px;background:#fff;z-index:6;box-shadow:5px 0 20px #0001}.nav-open nav{display:block}.top{padding:0 14px;gap:8px}.top span{display:none}main{padding:25px 17px}h1{font-size:25px}.flow{grid-template-columns:repeat(2,minmax(0,1fr))}.meta-row{gap:12px}.table-wrap table{min-width:650px}}@media print{.top,nav,.actions,.cli,.review-check,.flow{display:none}.shell{display:block}main{padding:0;max-width:none}.step-panel[hidden]{display:block!important}.step-panel{break-before:page}.table-wrap{overflow:visible}dialog{display:none}}"""
page = (
    """<!doctype html><!-- SPDX-License-Identifier: MIT --><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AutoOntology · 20시 리뷰</title><style>"""
    + css
    + """</style></head><body><header class="top"><button id="nav-toggle" aria-label="목차 접기·펼치기" aria-expanded="true">☰</button><strong>AutoOntology / 20시 리뷰</strong><span>LOCAL · FROZEN APPLICATION · """
    + code[:7]
    + """</span><button id="print">인쇄 / PDF</button></header><div class="shell"><nav aria-label="리뷰 목차"><div class="nav-group">REVIEW</div><a href="#start">리뷰 시작</a><a href="#workflow">전체 운영 흐름</a><a href="#scope">완료·미완료</a><a href="#next">다음 논의</a><a href="#recovery">실행·복구</a><div class="nav-group">EVIDENCE</div><a href="../autoontology-statechart-guide.html">상태차트 설명서 ↗</a><a href="../autoontology-illustrated-guide.html">구조 일러스트 ↗</a><a href="../ontology-lifecycle.html">전체 라이프사이클 ↗</a><a href="../changes/index.html">단계별 기록·캡처 ↗</a></nav><main><section id="start"><p class="eyebrow">KNOWLEDGE OS · REVIEW EDITION</p><h1>지식이 들어오고, 판단이 남고,<br>다음 운영으로 이어지는 흐름</h1><p class="intro">초기 구축에서 끝나지 않는 조직 지식체계. 현재 구현은 실제 로컬 화면에서 확인하고, 아직 없는 학습·자율성·소비 계약은 별도로 논의합니다.</p><div class="meta-row"><span>운영본 <b>r8 유지</b></span><span>가상 후보 <b>r"""
    + str(candidate)
    + ''' · 확인 대기</b></span><span>회귀 검증 <b>Python 1,630 / UI 202</b></span><span>실제 조직 기준선 <b>미측정</b></span></div><div class="actions"><a class="primary" href="'''
    + base
    + """?lang=ko" target="_blank" rel="noopener">리뷰 작업 공간 열기 ↗</a><a class="secondary" href="http://127.0.0.1:7830/ui/?lang=ko" target="_blank" rel="noopener">기존 로컬 작업 공간 ↗</a><a class="secondary" href="../app/docs/REVIEW_READINESS.md">전체 요구사항 감사</a></div><p class="note">리뷰 앱은 별도 파일·DB·포트의 복사본입니다. <strong>review 계정</strong>으로 로그인하며 비밀번호는 채팅에서 안내합니다. 추가한 규정·업무·질문은 명시적인 가상 사례입니다. 새 후보의 모델 구축·평가·운영 게시를 실행한 것으로 표시하지 않았고, 공개 배포도 하지 않았습니다.</p></section><section id="workflow" class="block"><div class="panel-heading"><div><p class="eyebrow">THE OPERATING LOOP</p><h2>화면을 따라 같은 업무를 끝까지 보기</h2></div><span class="progress" id="progress">0 / 7 확인</span></div><div class="flow" role="tablist" aria-label="전체 운영 흐름">"""
    + "".join(flow)
    + """</div>"""
    + "".join(panels)
    + """</section><section id="scope" class="block"><p class="eyebrow">VERIFIED SCOPE</p><h2>현재 도달한 부분과 남은 부분</h2><div class="table-wrap"><table><thead><tr><th>영역</th><th>상태</th><th>검증 범위와 다음 과제</th></tr></thead><tbody>"""
    + rows
    + """</tbody></table></div><p class="note">이 표는 전체 계획 완료율을 뜻하지 않습니다. 실제 공급자 호출, 실제 파일/DB, 제어된 builder, 유한 상태 모델을 구분해 기록했습니다. 미측정 조직 성과를 시간 절감 수치로 만들지 않았습니다.</p><div class="links"><a href="../changes/stage-22-continuous-collection/index.html">수집: 실제 UI 8장·복구 검증</a><a href="../changes/stage-21-interaction-speed/index.html">UI/CLI 실제 지연 실험</a><a href="../changes/stage-12-identity-administration/index.html">승인된 실제 Codex 답변 검증</a><a href="../changes/stage-09-publication-sql/index.html">SQL·CSV·복원 검증</a></div></section><section id="next" class="block"><p class="eyebrow">DISCUSSION</p><h2>20시에 결정할 다음 순서</h2><div class="discussion"><div><h3>01 · 결정이 다음 제안에 쓰이도록</h3><p>검증된 결정·맥락·근거·수정과 결과를 연결하고, 유사 판단의 재사용을 평가합니다. 기록을 남기는 것에서 지속적으로 보완하는 loop로 이어갑니다.</p></div><div><h3>02 · 확인을 줄일 수 있는 범위</h3><p>먼저 효과/소유자 복구 경계를 닫고 shadow 평가를 거칩니다. 사람이 명시한 정책·만료·한도·중지·복구 안에서 실행하고 예외를 인계합니다.</p></div><div><h3>03 · 조직의 실제 소비 업무</h3><p>지식 계약과 영향·내구성 있는 개념 identity를 연결합니다. 계약/청구 초안부터 업무 액션·별도 확인·중복 방지·보상으로 발전시킵니다.</p></div></div><p class="note">목표는 지식의 최신성·일관성·근거·재사용과 의사결정 효율입니다. UI·CLI 대기와 요청부터 사용 가능한 결과까지의 시간도 줄입니다. 개인 타이머나 자동화율 하나로 조직 성과를 판단하지 않습니다.</p></section><section id="recovery" class="block"><p class="eyebrow">LOCAL CONTROL & RECOVERY</p><h2>실행·중지와 되돌아갈 지점</h2><p>현재 앱 코드는 """
    + code[:7]
    + """에 고정했습니다. 리뷰용 UI 7840, backend 3141, Postgres 55436을 사용합니다. 기존 로컬 7830/3131/55433과 공개 서비스는 별도입니다.</p><pre>"""
    + E(control + " status\n" + control + " stop\n" + control + " start")
    + """</pre><p>Stage 00–22의 소스와 데이터는 각 비공개 복구 지점으로 돌아갈 수 있습니다. 코드 checkout만으로 데이터가 복원되지는 않습니다. 백업 자체를 실행하지 말고 별도 경로에 복원한 뒤 검증합니다.</p><div class="links"><a href="../RESTORE_POINTS.md">단계별 복구 지점</a><a href="verification.json">리뷰 UI/native 검증</a><a href="downloads-verification.json">원문·다운로드 해시</a><a href="prepared.json">가상 준비 항목과 운영본 유지 기록</a><a href="recovery.json">초기 리뷰 복원 검증</a></div><p class="meta">초기 리뷰 사본은 74개 SQLite 테이블·원문 7개·허용 Postgres 전체 행의 복원을 확인했습니다. runtime 백업과 inputs/control/login companion을 함께 보존하며, recovery.json에서 확인합니다. 로그인 정보와 토큰은 이 HTML에 포함하지 않습니다.</p></section><footer>생성 """
    + E(datetime.now().astimezone().isoformat(timespec="minutes"))
    + """ · 앱 """
    + code[:7]
    + """ 고정 · 모델·공개 배포 호출 없음 · 전체 계획 진행 중</footer></main></div><dialog id="zoom"><button id="close-zoom" aria-label="화면 확대 닫기">닫기</button><img alt="확대한 실제 화면"></dialog><script>const buttons=[...document.querySelectorAll('[data-step]')];function select(i){buttons.forEach((b,n)=>b.setAttribute('aria-selected',String(n===i)));document.querySelectorAll('.step-panel').forEach((p,n)=>p.hidden=n!==i)}buttons.forEach((b,i)=>{b.onclick=()=>select(i);b.setAttribute('role','tab');b.onkeydown=e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){e.preventDefault();const n=(i+(e.key==='ArrowRight'?1:buttons.length-1))%buttons.length;select(n);buttons[n].focus()}}});const zoom=document.getElementById('zoom');document.querySelectorAll('[data-enlarge]').forEach(b=>b.onclick=()=>{const image=document.getElementById(b.dataset.enlarge);zoom.querySelector('img').src=image.src;zoom.querySelector('img').alt=image.alt;zoom.showModal()});document.getElementById('close-zoom').onclick=()=>zoom.close();document.querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{const value=document.getElementById('command-'+b.dataset.copy).value;try{await navigator.clipboard.writeText(value);b.textContent='복사됨'}catch{b.textContent='위 명령을 선택해 복사하세요'}});function progress(){document.getElementById('progress').textContent=document.querySelectorAll('[data-check]:checked').length+' / 7 확인'}document.querySelectorAll('[data-check]').forEach(c=>c.onchange=progress);document.getElementById('nav-toggle').onclick=()=>{if(innerWidth<=700){document.body.classList.toggle('nav-open');document.getElementById('nav-toggle').setAttribute('aria-expanded',String(document.body.classList.contains('nav-open')))}else{document.body.classList.toggle('collapsed');document.getElementById('nav-toggle').setAttribute('aria-expanded',String(!document.body.classList.contains('collapsed')))}};document.querySelectorAll('nav a[href^="#"]').forEach(a=>a.onclick=()=>document.body.classList.remove('nav-open'));document.getElementById('print').onclick=()=>window.print();</script></body></html>"""
)
output = review / "index.html"
output.write_text(page)
print(
    json.dumps(
        {
            "html": str(output),
            "bytes": len(page.encode()),
            "captures": len(manifest["captures"]),
            "frozen_application": code,
            "external_runtime_dependencies": [],
            "credentials_included": False,
        }
    )
)

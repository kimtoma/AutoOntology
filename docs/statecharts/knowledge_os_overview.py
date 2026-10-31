# SPDX-License-Identifier: MIT
"""Whole-system reading map connecting the checked charts and all user scenarios.

This editorial navigation map is not an additional checked state model. It keeps
first-time setup, the repeated knowledge lifecycle, recovery and target expansion
separate while reusing the guide's existing scenarios and implementation labels.
"""

import html
import json

PHASES = [
    dict(
        id="setup",
        number="00",
        title="운영 환경 준비",
        who="최초 설치 · 운영 담당자",
        input="처음 설치하는 작업 공간",
        work="Codex 로그인, Ollama 임베딩, Postgres와 작업 공간을 확인합니다. 운영본이 없을 때만 최초 구축 안내에서 시작합니다.",
        output="소스를 연결할 수 있는 환경",
        boundary="설치는 보통 처음 한 번입니다. 이후의 첫 화면은 새 지식·검토 대기·운영 상태를 보는 운영 홈이 됩니다.",
        next="intake",
        scenarios=[1, 2, 3],
    ),
    dict(
        id="intake",
        number="01",
        title="입력 연결·원문 보존",
        who="데이터·문서 담당자",
        input="고객·계약·청구 표, DB, 규정·계획서·업무 문서와 개정본",
        work="정형 데이터와 비정형 문서를 소스로 연결합니다. 입력 버전과 원문을 보존해 이후 판단과 답변이 무엇을 근거로 했는지 확인할 수 있게 합니다.",
        output="출처와 처리 상태가 확인되는 입력",
        boundary="소스 등록과 새 문서 도착은 운영 반영이 아닙니다. 상시 감지·자동 수집 큐는 목표 설계이며 현재는 수동 변경 확인·구축 흐름이 중심입니다.",
        next="candidate",
        scenarios=[4, 5, 6],
    ),
    dict(
        id="candidate",
        number="02",
        title="후보 지식 준비",
        who="구축 담당자 · 목표: 준비 에이전트",
        input="보존한 입력과 현재 운영 지식",
        work="표의 구조와 문서의 개념·관계·규칙을 추출하고 의미를 연결합니다. 같은 업무 개념인지, 무엇이 추가·변경되는지 검토할 후보를 만듭니다.",
        output="변경 내용·원문 근거·처리 이력을 가진 미승인 후보",
        boundary="에이전트가 준비해도 운영 반영 권한을 얻지는 않습니다. 애매한 연결이나 실패한 처리는 보류·수정하며 현재 운영본은 유지합니다.",
        next="review",
        scenarios=[7, 8],
    ),
    dict(
        id="review",
        number="03",
        title="근거 검토·질문 검증",
        who="도메인 검토자 · 품질 담당자",
        input="후보와 실제 변경 근거, 영향받는 업무 질문",
        work="무엇이 왜 바뀌는지 확인하고 포함·보류·제외를 결정합니다. 대표 질문을 실행해 새 기준과 근거를 확인하고 반영할 내용을 준비합니다.",
        output="검토·검증을 마친 운영 반영 준비 내용",
        boundary="실패하거나 근거가 부족하면 02 후보 준비로 돌아갑니다. 현재 내용 확인·검증 절차는 제공되지만, 개인 신원에 연결된 승인과 엄밀한 규칙·예외 평가는 아직 연결 중입니다.",
        next="publish",
        scenarios=[14, 15, 8],
    ),
    dict(
        id="publish",
        number="04",
        title="확인한 운영본으로 전환",
        who="운영 반영 책임자",
        input="확인한 변경, 검증 결과, 준비된 검색·그래프·파일",
        work="검토한 지식·근거·검색 식별·산출물을 하나의 운영 버전으로 묶어 전환합니다. 운영 기록에서 어떤 내용을 반영했는지 확인합니다.",
        output="조직의 일반 조회가 함께 읽는 새 운영 버전",
        boundary="구축 완료가 반영 완료는 아닙니다. 준비나 검증이 실패하면 기존 운영본을 유지합니다. 정확한 내용에 연결된 개인 승인·권한 검사는 후속 연결 대상입니다.",
        next="use",
        scenarios=[2, 15, 16],
    ),
    dict(
        id="use",
        number="05",
        title="업무에서 활용·근거 확인",
        who="업무 사용자 · 분석·연계 담당자",
        input="승인된 운영 지식과 업무 질문",
        work="개념·그래프를 탐색하고, 근거를 찾아 답변을 확인합니다. SQL로 데이터를 조회하고 SPARQL·파일로 지식을 재사용합니다. 완료된 사용 기록에서 결과를 다시 확인합니다.",
        output="인용이 연결된 답변, 조회 결과·CSV, 그래프와 재사용 파일",
        boundary="RAG는 지식·원문 기반 답변, SQL은 실행 시점의 원본 DB 행 조회, SPARQL은 운영 그래프 질의입니다. 같은 운영본을 쓰더라도 과거 원본 DB 행 전체가 고정된다는 뜻은 아닙니다.",
        next="improve",
        scenarios=[9, 10, 11, 12],
    ),
    dict(
        id="improve",
        number="06",
        title="변화·피드백을 다음 개선으로",
        who="업무 사용자 · 운영 담당자 · 목표: 담당 에이전트",
        input="새 원문·규정 개정, 변경된 표, 답변 부족과 개선 요청",
        work="변화와 개선 요청을 운영 홈·검토함에서 확인하고 다음 후보 준비로 연결합니다. 사용 중 발견한 문제를 새 지식과 검증의 입력으로 되돌립니다.",
        output="다음 검토 대상과 그 근거 · 01부터 같은 운영 루프 반복",
        boundary="담당자·기한·우선순위·자동 수집·해결 운영본 연결은 완성 중인 영역입니다. 단순 확인을 해결 완료로 취급하지 않으며 개선이 끝날 때까지 추적해야 합니다.",
        next="intake",
        scenarios=[13, 17, 14, 6],
    ),
    dict(
        id="recovery",
        number="R",
        title="문제가 생기면 기록에서 복구",
        who="운영 책임자",
        input="실패·중지·응답 유실 또는 이전 지식으로 돌아가야 하는 이유",
        work="실행·사용·반영 기록에서 실제 완료 상태를 먼저 확인합니다. 필요하면 이전 지식의 차이·근거·검증을 담은 복원안을 준비하고 새 운영 버전으로 반영합니다.",
        output="확인 가능한 복구 결과와 보존된 이전 버전",
        boundary="불확실한 작업을 무작정 반복하지 않습니다. 지식 복원과 전체 데이터 백업 복구는 다르며, 복원안이 만료·변경되면 다시 확인합니다.",
        next="use",
        scenarios=[16, 19],
    ),
    dict(
        id="evolve",
        number="NEXT",
        title="조직 지식 OS로 확장",
        who="도메인 책임자 · 소비 업무·에이전트의 책임자",
        input="반복되는 운영 루프와 검증된 업무 수요",
        work="도메인별 기준·책임·권한을 관리하고, 보고서·앱·에이전트가 어떤 지식 계약을 쓰는지 연결합니다. 에이전트는 범위 안에서 준비하고 사람은 정확한 효과를 확인하는 방향으로 확장합니다.",
        output="목표: 재사용되는 지식 계약과 통제된 업무 실행",
        boundary="목표 설계입니다. 현재 완성된 조직 전체 권한·지식 계약·외부 업무 실행을 뜻하지 않습니다. 외부 발송·변경은 별도 승인·권한·중복 방지·부분 실패 복구가 필요합니다.",
        next="intake",
        scenarios=[18, 20, 21],
    ),
]

PRIMARY = {
    **{i: "setup" for i in [1, 2, 3]},
    **{i: "intake" for i in [4, 5, 6]},
    **{i: "candidate" for i in [7, 8]},
    **{i: "use" for i in [9, 10, 11, 12]},
    13: "improve",
    14: "review",
    15: "review",
    16: "recovery",
    17: "improve",
    18: "evolve",
    19: "recovery",
    20: "evolve",
    21: "evolve",
}


def render_overview(scenarios):
    """Return self-contained HTML/styles/behavior and machine-readable navigation."""
    assert set(PRIMARY) == {s["id"] for s in scenarios}
    assert {i for phase in PHASES for i in phase["scenarios"]} == set(PRIMARY)
    esc = html.escape
    labels = {
        "intake": ("표 + 문서", "정형·비정형 입력의 출처와 원문 보존"),
        "candidate": ("미승인 작업 공간", "개념·관계·규칙과 변경 후보 준비"),
        "review": ("사람의 확인 지점", "변경 근거와 영향·대표 질문 확인"),
        "publish": ("운영 버전", "확인한 지식·검색·파일을 함께 전환"),
        "use": ("조직의 일상 업무", "그래프·질문·SQL·근거·다운로드"),
        "improve": ("다음 개선의 입력", "새 지식·규정 개정·사용자 피드백"),
    }
    nodes = []
    for p in PHASES[1:7]:
        tag, detail = labels[p["id"]]
        nodes.append(
            f'<button type="button" class="os-node" id="os-{p["id"]}" data-os-phase="{p["id"]}" aria-controls="os-phase-detail" aria-pressed="false"><span class="os-step">{p["number"]} <span>{esc(tag)}</span></span><strong>{esc(p["title"])}</strong><span class="os-description">{esc(detail)}</span><span class="os-node-link">관련 시나리오 보기 ↗</span></button>'
        )
    markup = (
        """<section id="knowledge-os" aria-labelledby="os-title">
<p class="eyebrow">KNOWLEDGE OS · THE WHOLE LIFECYCLE</p>
<h1 id="os-title">조직의 지식을 모으고,<br>검토하고, 계속 운영하는 전체 흐름</h1>
<p class="lead">표와 문서에서 업무의 개념·관계·판단 기준을 연결하고, 검토한 버전을 조직의 공통 기준으로 운영합니다. 새 지식과 사용자의 피드백은 같은 검토·반영 과정을 거쳐 다음 운영본이 됩니다.</p>
<div class="os-meaning"><div><span>들어오는 것</span><strong>업무 데이터 + 업무 문서</strong><p>고객·계약·청구 표, DB, 규정·계획서·업무 문서</p></div><div><span>함께 관리하는 것</span><strong>지식 + 근거 + 운영 버전</strong><p>개념·관계·규칙을 원문·검증·변경 기록과 연결</p></div><div><span>업무에 돌아오는 것</span><strong>근거 있는 판단과 재사용</strong><p>질문·그래프·데이터 결과·파일, 이후 앱·에이전트 연계</p></div></div>
<div class="os-map-heading"><div><p class="eyebrow">ONE SETUP / CONTINUOUS OPERATIONS</p><h2>설치는 한 번, 지식 운영은 반복</h2></div><span class="meta">단계를 선택하면 설명과 상세 시나리오가 연결됩니다.</span></div>
<button type="button" class="os-setup" id="os-setup" data-os-phase="setup" aria-controls="os-phase-detail" aria-pressed="false"><span class="os-step">00 · 처음 한 번</span><strong>환경·모델·작업 공간 준비</strong><span>준비 후 아래 운영 루프로 →</span></button>
<div class="os-cycle" aria-label="조직 지식의 반복 운영 흐름">"""
        + "".join(nodes)
        + """</div>
<div class="os-loop"><span>06 개선에서 새 입력·개정으로 되돌아가 같은 루프를 반복합니다.</span><button type="button" data-os-phase="intake">↻ 01 입력부터 다시 보기</button></div>
<div class="os-live"><strong>현재 운영본은 계속 제공</strong><span>새 후보를 준비·보류·수정하는 동안에도 업무 사용자는 기존 운영본으로 질문·조회합니다. 확인한 새 운영본을 반영할 때 전환합니다.</span></div>
<div class="os-support"><button type="button" id="os-recovery" data-os-phase="recovery" aria-controls="os-phase-detail" aria-pressed="false"><span>모든 단계의 공통 안전망</span><strong>실패·중지·응답 유실 → 기록 확인·복구 ↗</strong></button><button type="button" id="os-evolve" data-os-phase="evolve" aria-controls="os-phase-detail" aria-pressed="false"><span>목표 설계 · 확장 방향</span><strong>도메인 기준·지식 계약·에이전트 준비와 사람 확인 ↗</strong></button></div>
<p class="meta">전체 구조를 읽기 위한 안내 지도입니다. 각 단계의 현재 제공·일부 구현·목표 설계는 연결된 시나리오에 표시합니다. 아래 6개 상태차트의 검사 범위를 확대한 것으로 해석하지 않습니다.</p>
<article id="os-phase-detail" class="os-phase-detail" aria-live="polite" aria-label="선택한 전체 흐름 단계"></article>
<div class="os-example"><p class="eyebrow">ONE EXAMPLE / END TO END</p><h3>계약 승인 기준이 3억원에서 2억원으로 바뀌었다면</h3><p>개정 문서 보존 → 계약 데이터와 연결한 후보 → 변경 근거·대표 질문 검토 → 새 기준으로 운영 반영 → 직원의 승인권자 질문·분석에 활용 → 부족한 답변을 다시 개선 요청으로 연결합니다.</p><div><a href="#scenario-14" data-open-scenario="14">개정 검토 14 ↗</a><a href="#scenario-15" data-open-scenario="15">질문 검증 15 ↗</a><a href="#scenario-10" data-open-scenario="10">업무 질문 10 ↗</a><a href="#scenario-17" data-open-scenario="17">피드백 17 ↗</a></div><p class="meta">금액·시행일·예외를 엄밀하게 구조화한 규칙 평가와 해결 운영본까지의 추적은 후속 구현입니다.</p></div>
<div class="os-bottom"><p><strong>전체 루프의 목표</strong> · 변경·요청이 들어온 순간부터 승인된 결과를 사용할 때까지의 총시간과 사람의 실작업 시간을 줄입니다. 대기·검토·실패·재작업도 함께 봅니다.</p><a href="#outcomes">시간 측정 기준 보기 →</a><a href="#overview">상세 설명서 읽는 방법 →</a></div>
</section>"""
    )
    styles = """
#knowledge-os{padding-bottom:8px}.os-meaning{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));border-block:1px solid #e5e5e5;margin:24px 0 32px}.os-meaning>div{padding:15px 18px 14px 0}.os-meaning>div+div{border-left:1px solid #e5e5e5;padding-left:18px}.os-meaning span,.os-support span{display:block;color:#777;font-size:10px;margin-bottom:4px}.os-meaning strong{font-size:13px;font-weight:550}.os-meaning p{font-size:11px;margin:4px 0}.os-map-heading{display:flex;justify-content:space-between;gap:20px;align-items:end;margin-bottom:14px}.os-map-heading h2{font-size:20px;margin:3px 0 0}.os-setup{display:flex;gap:16px;align-items:center;width:100%;text-align:left;border-radius:4px 4px 0 0;padding:13px 16px;background:#fafafa}.os-setup strong{font-size:12px;font-weight:550}.os-setup>span:last-child{margin-left:auto;font-size:10px;color:#777}.os-step{font:11px/1.6 Geist,Pretendard,sans-serif;color:#666}.os-step>span{margin-left:8px;font-size:10px;color:#777}.os-cycle{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px;padding:22px 0;grid-template-areas:"intake candidate review" "improve use publish"}.os-node{position:relative;grid-area:var(--area);padding:14px 16px;text-align:left;border:1px solid #dcdcdc;min-height:144px;display:flex;flex-direction:column;align-items:start;background:white}.os-node strong{font-size:15px;line-height:1.6;font-weight:550;margin:7px 0}.os-description{font-size:11px;color:#666}.os-node-link{font-size:10px;color:#145b9a;margin-top:8px}.os-node::after{position:absolute;z-index:1;background:white;color:#888;font-size:16px;pointer-events:none}#os-intake{--area:intake}#os-candidate{--area:candidate}#os-review{--area:review;border-color:#cfb87b;background:#fffdf9}#os-publish{--area:publish}#os-use{--area:use}#os-improve{--area:improve}#os-intake::after,#os-candidate::after{content:"→";right:-22px;top:54px}#os-review::after{content:"↓";bottom:-25px;left:50%}#os-publish::after,#os-use::after{content:"←";left:-22px;top:54px}.os-node[aria-pressed=true],.os-setup[aria-pressed=true],.os-support button[aria-pressed=true]{border-color:#246caa;box-shadow:inset 0 0 0 1px #246caa;background:#f5f9fc}.os-loop{display:flex;gap:12px;align-items:center;justify-content:space-between;border-block:1px solid #eee;padding:10px 0;color:#666;font-size:11px}.os-loop button{font-size:10px;white-space:nowrap}.os-live{display:flex;align-items:start;gap:16px;padding:14px 16px;margin-top:16px;border-left:2px solid #39749e;background:#f6f9fc}.os-live strong{font-size:12px;font-weight:550;white-space:nowrap}.os-live span{font-size:11px;color:#52616c}.os-support{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}.os-support button{text-align:left;padding:12px 14px;background:white}.os-support button:last-child{border-style:dashed;background:#fcfbf8}.os-support strong{font-size:11px;font-weight:500}.os-phase-detail{border:1px solid #ddd;border-radius:4px;margin-top:18px;padding:20px 22px}.os-phase-detail h3{font-size:18px}.os-phase-meta{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.os-phase-detail dl{display:grid;grid-template-columns:76px minmax(0,1fr);gap:8px 14px;font-size:12px;margin:18px 0}.os-phase-detail dt{font-size:10px;color:#888}.os-phase-detail dd{margin:0;color:#444}.os-related{border-top:1px solid #eee;margin-top:16px;padding-top:14px}.os-related p{font-size:11px;color:#777}.os-related-links{display:flex;gap:8px;flex-wrap:wrap}.os-related-links a{display:inline-flex;gap:8px;align-items:center;border:1px solid #e3e3e3;border-radius:4px;padding:7px 10px;font-size:11px;text-decoration:none;color:#333}.os-related-links a:hover{background:#f5f7f9}.os-related-links .badge{font-size:9px}.os-phase-next{margin-top:16px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}.os-phase-next span{font-size:10px;color:#888}.os-example{border-block:1px solid #e5e5e5;margin-top:26px;padding:22px 0}.os-example p{font-size:12px;max-width:950px}.os-example>div{display:flex;gap:18px;flex-wrap:wrap;font-size:11px}.os-example .meta{font-size:10px}.os-bottom{font-size:12px;padding:12px 0}.os-bottom>a{display:inline-block;margin-right:18px;font-size:11px}.scenario-os-context{border-bottom:1px solid #eee;margin:0 0 17px;padding:0 0 12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap;color:#777;font-size:10px}.scenario-os-context a{font-size:11px}.os-phase-detail:focus{outline:2px solid #246caa;outline-offset:3px}@media(max-width:1050px){.os-map-heading{display:block}.os-map-heading .meta{display:block;margin-top:6px}.os-node{padding:12px;min-height:162px}.os-step>span{display:block;margin-left:0}.os-node strong{font-size:13px}}@media(max-width:700px){.os-meaning{grid-template-columns:1fr}.os-meaning>div,.os-meaning>div+div{padding:12px 0;border-left:0}.os-meaning>div+div{border-top:1px solid #eee}.os-setup{display:grid;gap:4px}.os-setup>span:last-child{margin-left:0}.os-cycle{grid-template-columns:1fr;grid-template-areas:"intake" "candidate" "review" "publish" "use" "improve";gap:23px}.os-node{min-height:0;padding:13px 16px}.os-step>span{display:inline;margin-left:8px}.os-node strong{font-size:15px}.os-node::after,#os-intake::after,#os-candidate::after,#os-review::after,#os-publish::after,#os-use::after{content:"↓";left:50%;right:auto;top:auto;bottom:-26px}.os-node:last-child::after{content:none}.os-loop,.os-live{display:block}.os-loop button{margin-top:8px}.os-live span{display:block;margin-top:5px}.os-support{grid-template-columns:1fr}.os-phase-detail{padding:16px}.os-phase-detail dl{grid-template-columns:1fr;gap:4px}.os-phase-detail dt{margin-top:8px}.os-related-links{display:grid}.os-related-links a{flex-wrap:wrap}.os-related-links .badge{white-space:normal}}@media print{.os-node-link,.os-phase-next,.os-loop button{display:none}.os-node{break-inside:avoid}.os-cycle{gap:16px}.os-phase-detail{break-inside:avoid}.os-related-links a{border:0;padding:0}.os-meaning,.os-support{break-inside:avoid}}
"""
    navigation = {
        "kind": "editorial whole-system map; not an additional TLC model",
        "phases": PHASES,
        "primary_phase_by_scenario": PRIMARY,
    }
    payload = json.dumps(navigation, ensure_ascii=False).replace("</", "<\\/")
    script = r"""
const OS_MAP=__OS_DATA__;
const osPhase=id=>OS_MAP.phases.find(p=>p.id===id);
function showOsPhase(id,{scroll=false,focus=false}={}){
 const p=osPhase(id);if(!p)return;
 document.querySelectorAll('[data-os-phase][aria-pressed]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.osPhase===id)));
 const next=osPhase(p.next),panel=document.getElementById('os-phase-detail');panel.dataset.phase=id;
 panel.innerHTML=`<div class="os-phase-meta"><p class="eyebrow">WHOLE FLOW / ${esc(p.number)}</p><span class="meta">${esc(p.who)}</span></div><h3>${esc(p.title)}</h3><dl><dt>들어오는 것</dt><dd>${esc(p.input)}</dd><dt>이 단계의 일</dt><dd>${esc(p.work)}</dd><dt>남는 것</dt><dd>${esc(p.output)}</dd><dt>확인·예외</dt><dd>${esc(p.boundary)}</dd></dl><div class="os-related"><p>여기서 이어지는 상세 시나리오 · 선택하면 UI·CLI·상태도·복구 경로가 열립니다.</p><div class="os-related-links">${p.scenarios.map(id=>{const s=DATA.scenarios.find(s=>s.id===id);return `<a href="#scenario-${id}" data-open-scenario="${id}"><span>${String(id).padStart(2,'0')} · ${esc(s.title)}</span><span class="badge ${s.coverage}">${esc(DATA.status[s.coverage])}</span></a>`}).join('')}</div></div><div class="os-phase-next"><button type="button" data-os-phase="${next.id}">다음 연결 · ${esc(next.number)} ${esc(next.title)} →</button><span>문서 탐색용 · 실제 작업 실행 없음</span></div>`;
 if(scroll)panel.scrollIntoView({behavior:'instant',block:'start'});
 if(focus){panel.tabIndex=-1;panel.focus({preventScroll:true});}
}
function attachOsContext(id,phaseId=OS_MAP.primary_phase_by_scenario[id]){
 const p=osPhase(phaseId);if(!p)return;
 document.querySelector('#scenario-detail .scenario-os-context')?.remove();
 const context=document.createElement('div');context.className='scenario-os-context';
 context.innerHTML=`<span>전체 흐름에서의 위치</span><a href="#os-${p.id}" data-return-os="${p.id}">${esc(p.number)} ${esc(p.title)}로 돌아가기 ↑</a>`;
 document.getElementById('scenario-detail').prepend(context);
}
const chooseDetailedScenario=choose;
function returnToOsMap(id){showOsPhase(id);document.querySelector('.os-map-heading').scrollIntoView({behavior:'instant',block:'start'});const node=document.getElementById('os-'+id);if(node)node.focus({preventScroll:true});}
choose=function(id){chooseDetailedScenario(id);attachOsContext(id);showOsPhase(OS_MAP.primary_phase_by_scenario[id]);};
function openOsScenario(id,{historyEntry=true,focus=true,fromPhase=null}={}){
 if(!DATA.scenarios.some(s=>s.id===id))return;
 document.getElementById('scenario-search').value='';document.getElementById('scenario-filter').value='all';filter();choose(id);
 const phase=fromPhase&&osPhase(fromPhase)?.scenarios.includes(id)?fromPhase:OS_MAP.primary_phase_by_scenario[id];attachOsContext(id,phase);showOsPhase(phase);
 if(historyEntry)history.pushState({osFrom:phase},'','#scenario-'+id);
 document.getElementById('scenarios').scrollIntoView({behavior:'instant',block:'start'});
 if(focus){const heading=document.querySelector('#scenario-detail h3');heading.tabIndex=-1;heading.focus({preventScroll:true});}
}
document.addEventListener('click',event=>{
 const scenario=event.target.closest('[data-open-scenario]');
 if(scenario){event.preventDefault();openOsScenario(Number(scenario.dataset.openScenario),{fromPhase:scenario.closest('#os-phase-detail')?.dataset.phase});return;}
 const back=event.target.closest('[data-return-os]');
 if(back){event.preventDefault();history.pushState(null,'','#os-'+back.dataset.returnOs);returnToOsMap(back.dataset.returnOs);return;}
 const phase=event.target.closest('[data-os-phase]');
 if(phase){showOsPhase(phase.dataset.osPhase,{scroll:true,focus:true});}
});
function restoreOsHash(){const scenario=location.hash.match(/^#scenario-(\d+)$/);if(scenario){openOsScenario(Number(scenario[1]),{historyEntry:false,fromPhase:history.state?.osFrom});return;}const phase=location.hash.match(/^#os-([a-z]+)$/);if(phase&&osPhase(phase[1]))returnToOsMap(phase[1]);}
window.addEventListener('popstate',restoreOsHash);window.addEventListener('hashchange',restoreOsHash);
showOsPhase('intake');attachOsContext(selected);restoreOsHash();
""".replace("__OS_DATA__", payload)
    return markup, styles, script, navigation

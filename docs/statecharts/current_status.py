# SPDX-License-Identifier: MIT
"""Current editorial status beside preserved historical model-check evidence."""

import html
import shutil
from pathlib import Path

APPLICATION_REFERENCE = "b6d31ac6464c995aeb3058af1f209f64aefde539"


def render_current_status(source: Path, output: Path) -> str:
    destination = output / "Collection-verified"
    destination.mkdir(exist_ok=True)
    for name in ("chart.svg", "chart.mmd", "report.md", "tlc.out"):
        shutil.copy2(source / "Collection-verified" / name, destination / name)
    svg = (source / "Collection-verified/chart.svg").read_text()
    mmd = html.escape((source / "Collection-verified/chart.mmd").read_text())
    rows = [
        (
            "사람·에이전트 신원과 정확한 확인",
            "구현·범위 검증",
            "개인 인증·도메인 읽기·저장 결과·정확한 업무 검토 묶음과 철회. 초기 소유자 복구·전체 효과 journal·최종 전면 감사는 남아 있음",
            "changes/stage-20-host-boundaries/index.html",
        ),
        (
            "업무·규칙·평가·피드백",
            "구현·범위 검증",
            "담당자·기한·시행일·금액/권한 필드·버전 평가·해결 운영본·재개. 자동 의미 판단·개념 합치기/나누기와 소비 영향은 남아 있음",
            "changes/stage-19-feedback-ax/index.html",
        ),
        (
            "UI·CLI 대기",
            "실측",
            "첫 업무 데이터 269.6→182.1ms, 제어한 2초 지연 2150.4→133.6ms, native 업무 조회 193.0→79.4ms. 로컬 표본이며 조직 절감 측정은 아님",
            "changes/stage-21-interaction-speed/index.html",
        ),
        (
            "지속 수집·후보 준비 큐",
            "구현·범위 검증",
            "실제 폴더·Postgres 관찰, 중복 방지·재시도·중단 복구·담당자 outbox. 준비 빌드 복구는 제어된 builder 시험이며 수집이 게시 권한은 아님",
            "changes/stage-22-continuous-collection/index.html",
        ),
        (
            "결정에서 학습하는 자율 운영",
            "미완료",
            "검토·업무 결정 이력은 자동 기록된다. 이를 학습·평가해 명시적 정책 범위에서 실행하는 지속 agent loop와 예외 인계는 추가 개발이 필요하다",
            "review-20261031/index.html",
        ),
        (
            "조직 지식 OS 확장",
            "미완료",
            "도메인 지식 카탈로그·소비 계약·영향·내구성 있는 개념 identity·업무 액션/보상 계약을 이어서 구현한다",
            "knowledge-os-roadmap.html",
        ),
    ]
    table = "".join(
        "<tr><td>"
        + html.escape(a)
        + "</td><td>"
        + html.escape(b)
        + "</td><td>"
        + html.escape(c)
        + '</td><td><a href="'
        + html.escape(d)
        + '">근거 보기</a></td></tr>'
        for a, b, c, d in rows
    )
    return (
        '<section id="current-status" class="current-status"><p class="eyebrow">CURRENT · 2026.10.31 · b6d31ac</p><h2>현재 구현과 과거 상태 모델을 구분해서 읽기</h2><p>전체 목표는 조직 지식의 최신성·일관성·근거·재사용·의사결정 효율입니다. 초기 사람 검증 → 결정 자동 기록 → 지속 agent loop → 정책 범위의 자율 실행과 사람 예외 확인으로 발전시킵니다. 전체 로드맵 완료를 뜻하지 않습니다.</p><div class="table-wrap"><table><thead><tr><th>영역</th><th>상태</th><th>현재 경계</th><th>실행 근거</th></tr></thead><tbody>'
        + table
        + '</tbody></table></div><p class="note">아래 여섯 기존 TLA+ 모델은 <strong>6896be4 시점의 역사적 검증</strong>입니다. 당시 Publication의 개인 인증 위반은 그 코드에서 재현한 결과이며, 최신 코드가 같은 결함을 가진다는 주장이 아닙니다. 새 개인 승인 경계는 Stage 10–22의 실행 검증을 함께 보세요. 과거 증거와 불변식은 덮어쓰지 않았습니다.</p><section id="collection-current"><h3>새 수집 루프 · 배정과 늦은 결과 차단</h3><p>소스별 현재 입력 → 배정 작업자 → 중복 없는 순번 → 후보 준비/관찰 → 완료 또는 담당자 인계. 운영 게시 권한은 별도입니다.</p><div class="diagram">'
        + svg
        + '</div><p class="meta">별도 유한 모델: 38개 상태·86개 단계, 표시 상태 8개·화살표 18개. 중복 준비/완료, 효과 직전 권한, 완료 근거, 게시 불가와 deadlock 규칙 6개 통과. 한 작업·한 번의 세대 교체만 모델링하며 실제 공급자·파일/DB 전체 원자성이나 의미적 정답을 증명하지 않습니다.</p><details><summary>Mermaid 원문과 재현 자료</summary><pre><code class="language-mermaid">'
        + mmd
        + '</code></pre><p><a href="statecharts/Collection-verified/chart.mmd">Mermaid 다운로드</a> · <a href="statecharts/Collection-verified/chart.svg">SVG 다운로드</a> · <a href="statecharts/Collection-verified/report.md">TLC 보고서</a> · <a href="changes/stage-22-continuous-collection/index.html">실제 UI/native 동작</a></p></details></section></section>'
    )

import { koAX } from "@/i18n/ko.ax"
import { koQuality } from "@/i18n/ko.quality"
import type { enIdentity } from "@/i18n/en.identity"
import { koAccess } from "@/i18n/ko.access"
import { koCases } from "@/i18n/ko.cases"

export const koIdentity: Record<keyof typeof enIdentity, string> = {
  ...koAX,
  ...koQuality,
  "identity.authorityChanged": "접근 권한이 변경되어 실행 중 요청을 중단하고 화면을 새로 확인했습니다. 저장된 결과도 현재 권한으로 확인합니다.",
  ...koAccess,
  ...koCases,
  "identity.scope.title": "내 접근 범위의 지식",
  "identity.scope.hint": "읽을 수 있는 소스로 뒷받침된 완전한 항목만 표시합니다. 여러 소스가 합쳐진 항목은 모든 기여 소스의 접근 권한이 필요합니다.",
  "identity.scope.exports": "현재 접근 범위의 승인된 지식을 Turtle·JSON-LD·GraphML로 다운로드합니다.",
  "identity.scope.legacy": "과거 소스 식별이 기록되지 않아 일부 지식이 제한됩니다. 관리자에게 입력 계보와 접근 분류 확인을 요청하세요.",
  "identity.checking": "작업 공간 접근 권한을 확인하고 있습니다…",
  "identity.title": "작업 공간에 로그인",
  "identity.hint": "작업 공간 관리자가 부여한 개인 계정으로 로그인하세요.",
  "identity.username": "사용자 이름",
  "identity.password": "비밀번호",
  "identity.signIn": "로그인",
  "identity.signingIn": "계정을 확인하고 있습니다…",
  "identity.signOut": "로그아웃",
  "identity.signingOut": "로그아웃 중…",
  "identity.signedIn": "{name} 계정으로 접속 중",
  "identity.invalid": "사용자 이름과 비밀번호를 확인한 뒤 다시 시도하세요.",
  "identity.cooldown": "로그인 시도가 많습니다. {seconds}초 뒤 다시 시도하세요.",
  "identity.unavailable": "작업 공간 접근 권한을 확인하지 못했습니다.",
  "identity.remedy": "연결을 다시 확인하세요. 계속되면 로컬 작업 공간 관리자에게 로그인 설정 확인이나 복구를 요청하세요.",
  "identity.retry": "연결 다시 확인",
  "identity.loginFailed": "로그인을 완료하지 못했습니다. 연결을 확인한 뒤 다시 시도하세요.",
  "identity.logoutFailed": "로그아웃 완료를 확인하지 못했습니다. 브라우저를 떠나기 전에 다시 시도하세요.",
  "identity.denied": "현재 권한으로 볼 수 없는 화면입니다.",
  "identity.deniedHint": "이용 가능한 화면으로 이동하거나 관리자에게 역할과 접근 범위 확인을 요청하세요.",
}

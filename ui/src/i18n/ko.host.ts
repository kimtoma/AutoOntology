import type { enHost } from "@/i18n/en.host"
export const koHost:Record<keyof typeof enHost,string> = {
  "host.title": "호스트 입력 경로 승인",
  "host.hint": "개인 인증 작업 공간은 승인한 업무 폴더·파일, 업로드와 샘플에서만 새 입력을 읽습니다. 인증 파일과 링크 입력은 제외됩니다. 소스 등록만으로 호스트 접근 권한이 생기지 않습니다.",
  "host.enforced": "이 개인 인증 작업 공간에 입력 경로 제한이 적용됩니다.",
  "host.legacy": "개인 인증 전입니다. 비밀 파일 제외는 적용되며 개인 인증 활성화 후 입력 루트 제한이 적용됩니다.",
  "host.default": "기본 입력 루트",
  "host.allowed": "허용",
  "host.revoked": "철회",
  "host.allow": "업무 입력 경로 승인",
  "host.revoke": "이 경로 승인 철회",
  "host.retained": "철회하면 이후 실시간 읽기를 차단합니다. 보존 원문과 게시 지식에는 각 접근 정책이 계속 적용됩니다. 다른 허용 루트에 포함되면 접근이 유지될 수 있습니다.",
  "host.allowHint": "기존 업무 폴더나 파일 하나를 승인합니다. 편집자는 이 범위에서 입력을 등록할 수 있지만 소스·도메인 권한은 별도로 필요합니다.",
  "host.revokeHint": "이후 읽기·해시·원문 수집은 승인을 다시 검사합니다. 기존 이력은 보존합니다.",
  "host.path": "업무 폴더·파일의 절대 경로",
  "host.reason": "업무 사유",
  "host.ack": "정확한 경로와 이후 입력에 부여되는 권한을 확인했습니다.",
  "host.apply": "이 호스트 정책 적용"
}

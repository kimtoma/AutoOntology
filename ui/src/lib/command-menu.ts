export const OPEN_COMMAND_MENU = "autoontology:open-command"
export function openCommandMenu() {
  document.dispatchEvent(new Event(OPEN_COMMAND_MENU))
}

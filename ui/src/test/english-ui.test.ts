/* The UI source is English. Korean appears only in the Korean dictionaries (src/i18n/ko*.ts),
   in the language switch's own name for Korean ("한국어"), and as sample-data identifiers named
   as such: "the 계약 table", "the 수납상태 column". Test fixtures are exempt. */

const sources = import.meta.glob<string>(["/src/**/*.{ts,tsx}", "!/src/test/**", "!/src/i18n/ko*.ts"],
                                         { query: "?raw", import: "default", eager: true })

it("has no Korean in the UI source outside the Korean dictionaries", () => {
  expect(Object.keys(sources).length).toBeGreaterThan(20)
  expect(Object.keys(sources)).toContain("/src/i18n/en.ts")
  const stray: string[] = []
  for (const [file, text] of Object.entries(sources)) {
    for (const m of text.matchAll(/[가-힣ㄱ-ㆎ]+/g)) {
      const before = text.slice(Math.max(0, m.index - 4), m.index)
      const after = text.slice(m.index + m[0].length, m.index + m[0].length + 8)
      if (m[0] === "한국어" && file === "/src/i18n/core.ts") continue
      if (before !== "the " || !/^ (table|column)\b/.test(after)) stray.push(`${file}: …${before}${m[0]}${after}…`)
    }
  }
  expect(stray).toEqual([])
})

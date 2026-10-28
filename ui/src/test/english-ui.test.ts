/* The UI is English-only. Korean appears only as sample-data identifiers, named as such:
   "the 계약 table", "the 수납상태 column". Test fixtures are exempt. */

const sources = import.meta.glob<string>(["/src/**/*.{ts,tsx}", "!/src/test/**"],
                                         { query: "?raw", import: "default", eager: true })

it("has no Korean in the UI source except named sample identifiers", () => {
  expect(Object.keys(sources).length).toBeGreaterThan(20)
  const stray: string[] = []
  for (const [file, text] of Object.entries(sources)) {
    for (const m of text.matchAll(/[가-힣ㄱ-ㆎ]+/g)) {
      const before = text.slice(Math.max(0, m.index - 4), m.index)
      const after = text.slice(m.index + m[0].length, m.index + m[0].length + 8)
      if (before !== "the " || !/^ (table|column)\b/.test(after)) stray.push(`${file}: …${before}${m[0]}${after}…`)
    }
  }
  expect(stray).toEqual([])
})

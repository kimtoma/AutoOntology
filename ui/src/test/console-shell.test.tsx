import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Route, Routes } from "react-router"
import { AppShell } from "@/components/app-shell"
import { CommandMenu } from "@/components/command-menu"
import { mockFetch, renderPage } from "@/test/utils"

function consoleAt(path: string) {
  mockFetch({
    "GET /status": { status: "ok" },
    "GET /ops": { workspace: "test-workspace", counts: { pending: 2, held: 1, feedback: 0 } },
    "GET /ops/connections": { counts: { total: 3 } },
  })
  return renderPage(
    <>
      <CommandMenu />
      <Routes>
        <Route element={<AppShell />}>
          <Route path="*" element={<p>Workspace content</p>} />
        </Route>
      </Routes>
    </>,
    path,
  )
}

it("keeps the grouped Ontology navigation current when viewing the graph", async () => {
  consoleAt("/graph")
  expect(await screen.findByText("test-workspace")).toBeInTheDocument()
  const navigation = screen.getAllByRole("navigation")[0]
  expect(within(navigation).getByRole("link", { name: "Ontology" })).toHaveAttribute(
    "aria-current",
    "page",
  )
  expect(within(navigation).getByRole("link", { name: "Knowledge sources" })).not.toHaveAttribute(
    "aria-current",
  )
})

it("opens quick navigation from the visible search and follows the chosen page", async () => {
  const user = userEvent.setup()
  consoleAt("/")
  await user.click(screen.getByRole("button", { name: "Go to" }))
  const dialog = await screen.findByRole("dialog", { name: "Go to" })
  await user.click(within(dialog).getByRole("option", { name: /Knowledge sources/ }))
  expect(screen.queryByRole("dialog")).toBeNull()
  const navigation = screen.getAllByRole("navigation")[0]
  expect(within(navigation).getByRole("link", { name: "Knowledge sources" })).toHaveAttribute(
    "aria-current",
    "page",
  )
})

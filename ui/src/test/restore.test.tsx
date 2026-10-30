import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import HistoryPage from "@/pages/history"
import { RestoreButton } from "@/components/restoration"
import { mockFetch, renderPage } from "@/test/utils"

const packet = { id:"proposal",status:"ready",confirmation:"restore r2 from r4 abc",sha256:"abc",packet:{
  target_revision:2,base_revision:4,reason:"Revert incorrect policy",expires_at:"2099-01-01T00:00:00Z",
  validation:{ok:true,checks_configured:true,indexed_elements:4,questions:[{id:1,question:"Approver?",answer:"Director [E1]",ok:true}]},
  changes:[{collection:"classes",key:"c",label:"Approval policy",kind:"changed",before_text:"Revised approval",after_text:"Original approval",fields:[{name:"definitions",before:["Revised approval"],after:["Original approval"]}]}],
  diff:{classes:{totals:{added:0,changed:1,removed:0},changed:[{id:"c",name:"Approval policy",fields:["definitions"]}]}},
} }

it("prepares a restoration and requires an explicit second confirmation to execute", async () => {
  const user=userEvent.setup(), onRun=vi.fn()
  const {calls}=mockFetch({"POST /ops/restores":packet,"POST /ops/restores/proposal/execute":{run_id:"restore-run"}})
  renderPage(<RestoreButton revision={2} onRun={onRun} />)
  await user.click(screen.getByRole("button",{name:"Prepare restoration"}))
  await user.type(screen.getByLabelText("Restoration reason"),"Revert incorrect policy")
  await user.click(screen.getByRole("button",{name:"Validate restoration"}))
  expect(await screen.findByText("Director [E1]")).toBeInTheDocument()
  const values=within(screen.getByRole("heading",{name:"Values that will change"}).closest("section")!)
  expect(values.getByText(/Revised approval/)).toBeInTheDocument()
  expect(values.getByText(/Original approval/)).toBeInTheDocument()
  expect(calls.some(c=>c.path.endsWith('/execute'))).toBe(false)
  await user.click(screen.getByRole("button",{name:"Review restoration effect"}))
  expect(calls.some(c=>c.path.endsWith('/execute'))).toBe(false)
  await user.click(screen.getByRole("button",{name:"Confirm this restoration"}))
  await waitFor(()=>expect(onRun).toHaveBeenCalledWith("restore-run"))
  const submitted=calls.find(c=>c.path.endsWith('/execute'))!
  expect(JSON.parse(String(submitted.init?.body))).toEqual({confirmation:packet.confirmation})
})

it("never offers execution for a blocked representative-question suite", async () => {
  const user=userEvent.setup()
  mockFetch({"POST /ops/restores":{...packet,status:"blocked",packet:{...packet.packet,validation:{...packet.packet.validation,ok:false}}}})
  renderPage(<RestoreButton revision={2} onRun={()=>{}} />)
  await user.click(screen.getByRole("button",{name:"Prepare restoration"}))
  await user.type(screen.getByLabelText("Restoration reason"),"Recheck old policy")
  await user.click(screen.getByRole("button",{name:"Validate restoration"}))
  expect(await screen.findByText("Restoration is blocked. Review the failed checks and prepare again.")).toBeInTheDocument()
  expect(screen.queryByRole("button",{name:"Confirm this restoration"})).toBeNull()
})

it("keeps failed restoration snapshots out of operational download and restore actions", async()=>{
  mockFetch({"GET /ops":{published_revision:4,candidate_revision:3,counts:{approved:0,pending:0,held:0,rejected:0},checks:[]},"GET /ops/connections":{items:[],counts:{total:0},unapplied:0},"GET /ops/restores":{items:[]},"GET /ontology/revisions":{revisions:[{revision:5,run_kind:"restore",run_status:"failed",operational:false,created_at:"",sources:[]},{revision:4,run_kind:"publish",run_status:"succeeded",operational:true,created_at:"",sources:[]}]}})
  renderPage(<HistoryPage />)
  const label=await screen.findByText(/r5 ·/)
  const row=within(label.closest('li')!)
  expect(row.getByText(/Unpublished preparation/)).toBeInTheDocument()
  expect(row.queryByRole('button',{name:'Prepare restoration'})).toBeNull()
  expect(row.queryAllByRole('link')).toHaveLength(0)
})

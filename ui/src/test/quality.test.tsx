import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QualityPanel } from "@/components/quality-panel"
import { mockFetch, renderPage } from "@/test/utils"
const version={id:"v1",suite_key:"contracts",version:1,previous_id:null,sha256:"b".repeat(64),actor:"owner",created_at:"2026-10-31T00:00:00Z",payload:{title:"Contract evaluation",description:"Approval rule",questions:[{id:"approver",question:"Who approves?",expected:"CEO",mode:"contains",forbidden:["Any employee"],minimum_citations:1}],assertions:[]}}
it("requires explicit confirmation of the entire evaluation version",async()=>{
 let active=false
 const {calls}=mockFetch({"GET /quality":()=>({active:active?[version]:[],versions:[version]}),"GET /quality/rules":{items:[],checks:null},"GET /quality/runs":{items:[]},"POST /quality/versions/v1/activate":()=>{active=true;return version}})
 renderPage(<QualityPanel/>)
 await userEvent.click(await screen.findByRole("button",{name:"Activate this version"}))
 const dialog=screen.getByRole("dialog")
 expect(within(dialog).getByText("Who approves?")).toBeVisible()
 const confirm=within(dialog).getByRole("button",{name:"Activate this version"});expect(confirm).toBeDisabled()
 await userEvent.click(within(dialog).getByRole("checkbox"));await userEvent.click(confirm)
 expect(await screen.findByRole("button",{name:"Remove from active evaluation"})).toBeVisible()
 expect(JSON.parse(String(calls.find(c=>c.path==="/quality/versions/v1/activate")?.init?.body))).toEqual({sha256:"b".repeat(64),confirm:true})
})

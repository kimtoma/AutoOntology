import { screen,within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { AXDashboard } from "@/components/ax-dashboard"
import { IdentityContext,type IdentitySession } from "@/lib/identity"
import { renderPage,mockFetch } from "@/test/utils"
const session:IdentitySession={principal:{id:"owner",username:"owner",name:"Owner",kind:"human",status:"active"},credential_id:"credential",expires_at:2000000000,policy_version:1,workspace:{workspace_id:"org",workspace_name:"Org",organization_id:"org"},grants:[],capabilities:{"ax.read":["*"],"ax.configure":["*"],"ax.record":["*"]}}
it("records an explicitly confirmed reported simulation without claiming savings",async()=>{
 const {calls}=mockFetch({"GET /ax":{cohorts:[],voided_baselines:0},"GET /ax/baselines":{items:[]},"POST /ax/baselines":{id:"sample"}})
 renderPage(<IdentityContext.Provider value={{session,signOut:vi.fn(),signingOut:false,logoutFailed:false}}><AXDashboard/></IdentityContext.Provider>)
 await userEvent.click(await screen.findByRole("button",{name:"Record manual baseline"}))
 const dialog=screen.getByRole("dialog")
 await userEvent.type(within(dialog).getByLabelText("Manual total duration (minutes)"),"30")
 await userEvent.type(within(dialog).getByLabelText("Manual human effort (minutes)"),"10")
 await userEvent.selectOptions(within(dialog).getByLabelText("Measurement scope"),"simulation")
 await userEvent.type(within(dialog).getByLabelText("Measurement reference and method"),"Explicit estimate, fixture only")
 const save=within(dialog).getByRole("button",{name:"Save baseline observation"});expect(save).toBeDisabled()
 await userEvent.click(within(dialog).getByRole("checkbox"));await userEvent.click(save)
 const body=JSON.parse(String(calls.find(c=>c.path==="/ax/baselines"&&c.init?.method==="POST")?.init?.body))
 expect(body.payload).toMatchObject({lead_seconds:1800,active_seconds:600,source:"reported",measurement_scope:"simulation"})
 expect(body.confirm).toBe(true);expect(body.payload.request_key).toBeTruthy()
 expect(screen.getByText("No measured outcomes yet. Classify cases, record work intervals and establish a manual baseline.")).toBeVisible()
})

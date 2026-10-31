import { screen,within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { CollectionPanel } from "@/components/collection-panel"
import { IdentityContext,type IdentitySession } from "@/lib/identity"
import { renderPage,mockFetch } from "@/test/utils"
const owner="a".repeat(32)
const session:IdentitySession={principal:{id:owner,username:"owner",name:"Owner",kind:"human",status:"active"},credential_id:"credential",expires_at:2000000000,policy_version:1,workspace:{workspace_id:"org",workspace_name:"Org",organization_id:"org"},grants:[],capabilities:{"collection.read":["*"],"collection.configure":["*"],"collection.work":["*"]}}
it("confirms the exact source, assigned worker and observation-only scope",async()=>{
 const {calls}=mockFetch({"GET /collection":{schedules:[],jobs:[],notices:[],workers:[]},"GET /collection/workers":{items:[{id:owner,name:"Owner",kind:"human"}]},"GET /sources":{sources:[{name:"rules",kind:"documents"}]},"POST /collection":{id:"scheduled"}})
 renderPage(<IdentityContext.Provider value={{session,signOut:vi.fn(),signingOut:false,logoutFailed:false}}><CollectionPanel/></IdentityContext.Provider>)
 const user=userEvent.setup();await user.click(screen.getByText("Continuous collection",{exact:true}));await user.click(await screen.findByRole("button",{name:"Schedule a source"}))
 const dialog=screen.getByRole("dialog");await user.selectOptions(within(dialog).getByLabelText("Source"),"rules");await user.type(within(dialog).getByLabelText("Business reason"),"Observe approved policy changes")
 const apply=within(dialog).getByRole("button",{name:"Apply collection scope"});expect(apply).toBeDisabled();await user.click(within(dialog).getByRole("checkbox"));await user.click(apply)
 const call=calls.find(c=>c.method==="POST"&&c.path==="/collection");expect(JSON.parse(String(call?.init?.body))).toMatchObject({source:"rules",worker_id:owner,mode:"observe",confirm:true})
 expect(calls.some(c=>c.path==="/ops/publish"||c.path==="/runs")).toBe(false)
})
it("does not start a worker just by opening collection status",async()=>{
 const {calls}=mockFetch({"GET /collection":{schedules:[],jobs:[],notices:[],workers:[]},"GET /collection/workers":{items:[]},"GET /sources":{sources:[]}})
 renderPage(<IdentityContext.Provider value={{session,signOut:vi.fn(),signingOut:false,logoutFailed:false}}><CollectionPanel/></IdentityContext.Provider>)
 await userEvent.click(screen.getByText("Continuous collection",{exact:true}));expect(await screen.findByText("No collection schedules yet.")).toBeVisible();expect(calls.some(c=>c.method==="POST")).toBe(false)
})

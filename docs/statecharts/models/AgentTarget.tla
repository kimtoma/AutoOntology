---- MODULE AgentTarget ----

EXTENDS Naturals, FiniteSets

VARIABLES phase, delegated, unexpired, fresh, consent, verifiedHuman, effects

vars == <<phase, delegated, unexpired, fresh, consent, verifiedHuman, effects>>

Init == phase = "assigned"
    /\ delegated = TRUE
    /\ unexpired = TRUE
    /\ fresh = TRUE
    /\ consent = FALSE
    /\ verifiedHuman = FALSE
    /\ effects = 0

Prepare == phase = "assigned" /\ delegated /\ unexpired
    /\ phase' = "preparing"
    /\ UNCHANGED <<delegated, unexpired, fresh, consent, verifiedHuman, effects>>

Ready == phase = "preparing"
    /\ phase' = "awaiting"
    /\ UNCHANGED <<delegated, unexpired, fresh, consent, verifiedHuman, effects>>

HumanApprove == phase = "awaiting"
    /\ phase' = "approved"
    /\ verifiedHuman' = TRUE
    /\ consent' = TRUE
    /\ UNCHANGED <<delegated, unexpired, fresh, effects>>

AgentSelfApproval == phase = "awaiting"
    /\ phase' = "blocked"
    /\ UNCHANGED <<delegated, unexpired, fresh, consent, verifiedHuman, effects>>

Reject == phase = "awaiting"
    /\ phase' = "rejected"
    /\ UNCHANGED <<delegated, unexpired, fresh, consent, verifiedHuman, effects>>

Revoke == phase \in {"assigned","preparing","awaiting","approved","executing"}
    /\ phase' = "blocked"
    /\ delegated' = FALSE
    /\ UNCHANGED <<unexpired, fresh, consent, verifiedHuman, effects>>

Expire == phase \in {"assigned","preparing","awaiting","approved","executing"}
    /\ phase' = "blocked"
    /\ unexpired' = FALSE
    /\ UNCHANGED <<delegated, fresh, consent, verifiedHuman, effects>>

Change == phase \in {"approved","executing"}
    /\ phase' = "blocked"
    /\ fresh' = FALSE
    /\ consent' = FALSE
    /\ UNCHANGED <<delegated, unexpired, verifiedHuman, effects>>

Execute == phase = "approved" /\ delegated /\ unexpired /\ fresh /\ consent /\ verifiedHuman
    /\ phase' = "executing"
    /\ UNCHANGED <<delegated, unexpired, fresh, consent, verifiedHuman, effects>>

Complete == phase = "executing" /\ delegated /\ unexpired /\ fresh /\ consent /\ verifiedHuman /\ effects = 0
    /\ phase' = "succeeded"
    /\ effects' = 1
    /\ UNCHANGED <<delegated, unexpired, fresh, consent, verifiedHuman>>

LostResponse == phase = "succeeded"
    /\ phase' = "uncertain"
    /\ UNCHANGED <<delegated, unexpired, fresh, consent, verifiedHuman, effects>>

InspectReceipt == phase = "uncertain"
    /\ phase' = "succeeded"
    /\ UNCHANGED <<delegated, unexpired, fresh, consent, verifiedHuman, effects>>

IdleStep == phase \in {"blocked", "rejected", "succeeded"} /\ UNCHANGED vars

Next == Prepare \/ Ready \/ HumanApprove \/ AgentSelfApproval \/ Reject \/ Revoke \/ Expire \/ Change \/ Execute \/ Complete \/ LostResponse \/ InspectReceipt \/ IdleStep

TypeOK == phase \in {"approved", "assigned", "awaiting", "blocked", "executing", "preparing", "rejected", "succeeded", "uncertain"}

VerifiedHumanApproval == effects = 1 => verifiedHuman /\ consent

ConsentBoundEffect == effects = 1 => delegated /\ unexpired /\ fresh

AtMostOnce == effects <= 1

====

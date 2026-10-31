---- MODULE Restoration ----

EXTENDS Naturals, FiniteSets

VARIABLES phase, head, graph, index, files, valid, exact, fresh, unexpired, commits

vars == <<phase, head, graph, index, files, valid, exact, fresh, unexpired, commits>>

Init == phase = "operating"
    /\ head = 0
    /\ graph = 0
    /\ index = 0
    /\ files = 0
    /\ valid = FALSE
    /\ exact = TRUE
    /\ fresh = TRUE
    /\ unexpired = TRUE
    /\ commits = 0

Prepare == phase = "operating"
    /\ phase' = "preparing"
    /\ UNCHANGED <<head, graph, index, files, valid, exact, fresh, unexpired, commits>>

Ready == phase = "preparing"
    /\ phase' = "ready"
    /\ valid' = TRUE
    /\ UNCHANGED <<head, graph, index, files, exact, fresh, unexpired, commits>>

PreparationFails == phase = "preparing"
    /\ phase' = "failed"
    /\ UNCHANGED <<head, graph, index, files, valid, exact, fresh, unexpired, commits>>

WrongConfirmation == phase = "ready"
    /\ phase' = "wrong"
    /\ exact' = FALSE
    /\ UNCHANGED <<head, graph, index, files, valid, fresh, unexpired, commits>>

ReadCorrectPacket == phase = "wrong"
    /\ phase' = "ready"
    /\ exact' = TRUE
    /\ UNCHANGED <<head, graph, index, files, valid, fresh, unexpired, commits>>

Expire == phase \in {"ready","executing"}
    /\ phase' = "expired"
    /\ unexpired' = FALSE
    /\ UNCHANGED <<head, graph, index, files, valid, exact, fresh, commits>>

ContextChanged == phase \in {"ready","executing"}
    /\ phase' = "stale"
    /\ fresh' = FALSE
    /\ UNCHANGED <<head, graph, index, files, valid, exact, unexpired, commits>>

Confirm == phase = "ready" /\ exact /\ valid /\ fresh /\ unexpired
    /\ phase' = "executing"
    /\ UNCHANGED <<head, graph, index, files, valid, exact, fresh, unexpired, commits>>

FailureBeforeCommit == phase = "executing"
    /\ phase' = "failed"
    /\ UNCHANGED <<head, graph, index, files, valid, exact, fresh, unexpired, commits>>

Commit == phase = "executing" /\ exact /\ valid /\ fresh /\ unexpired /\ commits = 0
    /\ phase' = "committed"
    /\ head' = 1
    /\ graph' = 1
    /\ index' = 1
    /\ files' = 1
    /\ commits' = 1
    /\ UNCHANGED <<valid, exact, fresh, unexpired>>

LostResponse == phase = "committed"
    /\ phase' = "uncertain"
    /\ UNCHANGED <<head, graph, index, files, valid, exact, fresh, unexpired, commits>>

RetrySame == phase = "uncertain"
    /\ phase' = "committed"
    /\ UNCHANGED <<head, graph, index, files, valid, exact, fresh, unexpired, commits>>

IdleStep == phase \in {"failed", "stale", "expired", "committed"} /\ UNCHANGED vars

Next == Prepare \/ Ready \/ PreparationFails \/ WrongConfirmation \/ ReadCorrectPacket \/ Expire \/ ContextChanged \/ Confirm \/ FailureBeforeCommit \/ Commit \/ LostResponse \/ RetrySame \/ IdleStep

TypeOK == phase \in {"committed", "executing", "expired", "failed", "operating", "preparing", "ready", "stale", "uncertain", "wrong"}

SamePublication == head = graph /\ head = index /\ head = files

NoDuplicateCommit == commits <= 1

ExactConfirmedCommit == commits = 1 => valid /\ exact /\ fresh /\ unexpired

====

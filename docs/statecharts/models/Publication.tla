---- MODULE Publication ----

EXTENDS Naturals, FiniteSets

VARIABLES phase, reviewed, checked, fresh, prepared, head, graph, index, files, verifiedHuman

vars == <<phase, reviewed, checked, fresh, prepared, head, graph, index, files, verifiedHuman>>

Init == phase = "empty"
    /\ reviewed = FALSE
    /\ checked = FALSE
    /\ fresh = TRUE
    /\ prepared = FALSE
    /\ head = 0
    /\ graph = 0
    /\ index = 0
    /\ files = 0
    /\ verifiedHuman = FALSE

Start == phase = "empty"
    /\ phase' = "checking"
    /\ UNCHANGED <<reviewed, checked, fresh, prepared, head, graph, index, files, verifiedHuman>>

Build == phase = "checking"
    /\ phase' = "building"
    /\ UNCHANGED <<reviewed, checked, fresh, prepared, head, graph, index, files, verifiedHuman>>

ConnectionFails == phase = "checking"
    /\ phase' = "blocked"
    /\ UNCHANGED <<reviewed, checked, fresh, prepared, head, graph, index, files, verifiedHuman>>

RetryConnection == phase = "blocked"
    /\ phase' = "checking"
    /\ UNCHANGED <<reviewed, checked, fresh, prepared, head, graph, index, files, verifiedHuman>>

CandidateReady == phase = "building"
    /\ phase' = "candidate"
    /\ UNCHANGED <<reviewed, checked, fresh, prepared, head, graph, index, files, verifiedHuman>>

Hold == phase = "candidate"
    /\ phase' = "held"
    /\ UNCHANGED <<reviewed, checked, fresh, prepared, head, graph, index, files, verifiedHuman>>

ResumeReview == phase = "held"
    /\ phase' = "candidate"
    /\ UNCHANGED <<reviewed, checked, fresh, prepared, head, graph, index, files, verifiedHuman>>

Accept == phase = "candidate"
    /\ phase' = "reviewed"
    /\ reviewed' = TRUE
    /\ fresh' = TRUE
    /\ prepared' = FALSE
    /\ UNCHANGED <<checked, head, graph, index, files, verifiedHuman>>

CheckPass == phase = "reviewed"
    /\ phase' = "validated"
    /\ checked' = TRUE
    /\ UNCHANGED <<reviewed, fresh, prepared, head, graph, index, files, verifiedHuman>>

CheckFails == phase = "reviewed"
    /\ phase' = "validation_failed"
    /\ checked' = FALSE
    /\ UNCHANGED <<reviewed, fresh, prepared, head, graph, index, files, verifiedHuman>>

Correct == phase = "validation_failed"
    /\ phase' = "candidate"
    /\ reviewed' = FALSE
    /\ UNCHANGED <<checked, fresh, prepared, head, graph, index, files, verifiedHuman>>

Prepare == phase = "validated" /\ reviewed /\ checked /\ fresh
    /\ phase' = "prepared"
    /\ prepared' = TRUE
    /\ UNCHANGED <<reviewed, checked, fresh, head, graph, index, files, verifiedHuman>>

ContextChanges == phase \in {"validated", "prepared"}
    /\ phase' = "candidate"
    /\ reviewed' = FALSE
    /\ checked' = FALSE
    /\ prepared' = FALSE
    /\ fresh' = FALSE
    /\ UNCHANGED <<head, graph, index, files, verifiedHuman>>

Confirm == phase = "prepared" /\ reviewed /\ checked /\ prepared /\ fresh
    /\ phase' = "published"
    /\ head' = 1
    /\ graph' = 1
    /\ index' = 1
    /\ files' = 1
    /\ UNCHANGED <<reviewed, checked, fresh, prepared, verifiedHuman>>

IdleStep == phase \in {"published"} /\ UNCHANGED vars

Next == Start \/ Build \/ ConnectionFails \/ RetryConnection \/ CandidateReady \/ Hold \/ ResumeReview \/ Accept \/ CheckPass \/ CheckFails \/ Correct \/ Prepare \/ ContextChanges \/ Confirm \/ IdleStep

TypeOK == phase \in {"blocked", "building", "candidate", "checking", "empty", "held", "prepared", "published", "reviewed", "validated", "validation_failed"}

SamePublication == head = graph /\ head = index /\ head = files

ReviewedBeforePublish == head = 1 => reviewed /\ checked /\ prepared /\ fresh

VerifiedHumanApproval == head = 1 => verifiedHuman

====

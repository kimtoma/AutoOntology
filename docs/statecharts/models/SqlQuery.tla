---- MODULE SqlQuery ----

EXTENDS Naturals, FiniteSets

VARIABLES phase, accepted, catalog, validSql, rows, trace, executions

vars == <<phase, accepted, catalog, validSql, rows, trace, executions>>

Init == phase = "idle"
    /\ accepted = FALSE
    /\ catalog = FALSE
    /\ validSql = FALSE
    /\ rows = FALSE
    /\ trace = "none"
    /\ executions = 0

Ask == phase = "idle"
    /\ phase' = "catalog"
    /\ accepted' = TRUE
    /\ trace' = "running"
    /\ UNCHANGED <<catalog, validSql, rows, executions>>

StopBeforeSend == phase = "idle"
    /\ phase' = "cancelled"
    /\ UNCHANGED <<accepted, catalog, validSql, rows, trace, executions>>

VerifyCatalog == phase = "catalog"
    /\ phase' = "planning"
    /\ catalog' = TRUE
    /\ UNCHANGED <<accepted, validSql, rows, trace, executions>>

CatalogMissing == phase = "catalog"
    /\ phase' = "failed"
    /\ trace' = "failed"
    /\ UNCHANGED <<accepted, catalog, validSql, rows, executions>>

PlanAllowed == phase = "planning"
    /\ phase' = "checked"
    /\ validSql' = TRUE
    /\ UNCHANGED <<accepted, catalog, rows, trace, executions>>

PlanRejected == phase = "planning"
    /\ phase' = "failed"
    /\ trace' = "failed"
    /\ UNCHANGED <<accepted, catalog, validSql, rows, executions>>

Execute == phase = "checked" /\ catalog /\ validSql
    /\ phase' = "executing"
    /\ executions' = executions + 1
    /\ UNCHANGED <<accepted, catalog, validSql, rows, trace>>

ReturnRows == phase = "executing"
    /\ phase' = "complete"
    /\ trace' = "succeeded"
    /\ rows' = TRUE
    /\ UNCHANGED <<accepted, catalog, validSql, executions>>

DatabaseFails == phase = "executing"
    /\ phase' = "failed"
    /\ trace' = "failed"
    /\ UNCHANGED <<accepted, catalog, validSql, rows, executions>>

Stop == phase \in {"catalog","planning","checked","executing"}
    /\ phase' = "cancelled"
    /\ trace' = "cancelled"
    /\ UNCHANGED <<accepted, catalog, validSql, rows, executions>>

Download == phase = "complete" /\ trace = "succeeded" /\ rows
    /\ phase' = "saved"
    /\ UNCHANGED <<accepted, catalog, validSql, rows, trace, executions>>

Reopen == phase = "saved"
    /\ phase' = "complete"
    /\ UNCHANGED <<accepted, catalog, validSql, rows, trace, executions>>

IdleStep == phase \in {"failed", "cancelled"} /\ UNCHANGED vars

Next == Ask \/ StopBeforeSend \/ VerifyCatalog \/ CatalogMissing \/ PlanAllowed \/ PlanRejected \/ Execute \/ ReturnRows \/ DatabaseFails \/ Stop \/ Download \/ Reopen \/ IdleStep

TypeOK == phase \in {"cancelled", "catalog", "checked", "complete", "executing", "failed", "idle", "planning", "saved"}

NoFalseSuccess == trace = "succeeded" => rows /\ executions = 1

NoQueryReplay == executions <= 1

ApprovedScopeOnly == executions = 1 => catalog /\ validSql

NoFailedDownload == phase = "saved" => trace = "succeeded"

====

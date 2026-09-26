-- =============================================================================
-- PLANT-LEVEL ROW SECURITY for DirectQuery users.
-- Power BI DirectQuery with Entra ID SSO sends the REAL user to Snowflake, so a
-- Snowflake ROW ACCESS POLICY filters rows per person — no duplicate rules in BI.
-- (Import models cannot use this: the refresh runs as a service user. The import
--  model applies the SAME entitlement table through Power BI RLS roles instead:
--  powerbi/.../roles/PlantScoped.tmdl.)
-- =============================================================================
USE ROLE SECURITYADMIN;
USE SCHEMA MFG_DW.SECURITY;

CREATE TABLE IF NOT EXISTS PLANT_ENTITLEMENT (
  USER_EMAIL VARCHAR NOT NULL,     -- Entra UPN, upper-cased
  PLANT_ID   VARCHAR NOT NULL,     -- '*' = all plants (group leadership)
  GRANTED_BY VARCHAR, GRANTED_TS TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);
-- Example entitlements (fictional users)
INSERT INTO PLANT_ENTITLEMENT (USER_EMAIL, PLANT_ID, GRANTED_BY) VALUES
  ('PLANTMGR.PUNE@NORTHFORGE.EXAMPLE', 'P01', 'SEED'),
  ('PLANTMGR.MONTERREY@NORTHFORGE.EXAMPLE', 'P02', 'SEED'),
  ('COO@NORTHFORGE.EXAMPLE', '*', 'SEED');

CREATE OR REPLACE ROW ACCESS POLICY RAP_PLANT AS (PLANT_ID_ARG VARCHAR) RETURNS BOOLEAN ->
     IS_ROLE_IN_SESSION('MFG_PUBLISHER_ROLE')         -- the pipeline must see everything to reconcile
  OR CURRENT_USER() = 'SVC_POWERBI_REFRESH'           -- import refresh: Power BI RLS applies downstream
  OR EXISTS (SELECT 1 FROM MFG_DW.SECURITY.PLANT_ENTITLEMENT e
             WHERE e.USER_EMAIL = UPPER(CURRENT_USER()) AND (e.PLANT_ID = PLANT_ID_ARG OR e.PLANT_ID = '*'));

ALTER VIEW MFG_DW.REPORTING.VW_PLANT_OEE_DAILY           ADD ROW ACCESS POLICY RAP_PLANT ON (PLANT_ID);
ALTER VIEW MFG_DW.REPORTING.VW_SHOPFLOOR_MACHINE_STATUS  ADD ROW ACCESS POLICY RAP_PLANT ON (PLANT_ID);
ALTER VIEW MFG_DW.REPORTING.VW_SCRAP_COST_DAILY          ADD ROW ACCESS POLICY RAP_PLANT ON (PLANT_ID);
ALTER VIEW MFG_DW.REPORTING.VW_EXEC_KPI_MONTHLY          ADD ROW ACCESS POLICY RAP_PLANT ON (PLANT_ID);
ALTER VIEW MFG_DW.REPORTING.VW_DEFECT_PARETO             ADD ROW ACCESS POLICY RAP_PLANT ON (PLANT_ID);
ALTER VIEW MFG_DW.REPORTING.VW_SUPPLIER_QUALITY_MONTHLY  ADD ROW ACCESS POLICY RAP_PLANT ON (PLANT_ID);

-- Entitlements are readable by the BI reader so the import model can load them for Power BI RLS.
GRANT USAGE ON SCHEMA MFG_DW.SECURITY TO ROLE MFG_BI_READER_ROLE;
GRANT SELECT ON TABLE MFG_DW.SECURITY.PLANT_ENTITLEMENT TO ROLE MFG_BI_READER_ROLE;

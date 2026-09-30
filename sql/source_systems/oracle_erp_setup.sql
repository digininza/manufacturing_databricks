-- sqlfluff:dialect:oracle
-- sqlfluff:ignore:parsing
-- ^ admin/DBA script: GRANT/ALTER ROLE/RESOURCE MONITOR forms are valid SQL that sqlfluff's dialect cannot parse yet.
/* =============================================================================
   SOURCE: Oracle 19c — ERP (schema ERP). Query-based incremental extraction.

   Why not log-based CDC on Oracle?  Options were:
     * Oracle GoldenGate / LogMiner-based CDC  -> licensing + DBA ownership; not approved for this phase
     * ADF has no native Oracle CDC connector (its CDC resource supports SQL Server/Azure SQL)
   So we use the ERP-maintained LAST_UPDATE_DATE column ("query-based CDC"),
   with the limitations documented in docs/CDC_EXPLAINED.md.
   ============================================================================= */

CREATE TABLE ERP.PLANTS (
    PLANT_ID          VARCHAR2(10)  PRIMARY KEY,
    PLANT_NAME        VARCHAR2(100) NOT NULL,
    CITY              VARCHAR2(60),
    COUNTRY_CODE      CHAR(2),
    TIMEZONE          VARCHAR2(40),
    LAST_UPDATE_DATE  DATE DEFAULT SYSDATE NOT NULL
);

CREATE TABLE ERP.PRODUCTS (
    PRODUCT_ID          VARCHAR2(20)  PRIMARY KEY,
    SKU                 VARCHAR2(30)  NOT NULL,
    PRODUCT_NAME        VARCHAR2(100),
    PRODUCT_FAMILY      VARCHAR2(40),
    UNIT_COST           NUMBER(12,2),
    STD_CYCLE_TIME_SEC  NUMBER(6),
    LAST_UPDATE_DATE    DATE DEFAULT SYSDATE NOT NULL
);

CREATE TABLE ERP.PRODUCTION_ORDERS (
    ORDER_ID          VARCHAR2(40) PRIMARY KEY,
    PRODUCT_ID        VARCHAR2(20) NOT NULL,
    PLANT_ID          VARCHAR2(10) NOT NULL,
    PLANNED_QTY       NUMBER(10)   NOT NULL,
    PLANNED_START     DATE,
    PLANNED_END       DATE,
    ORDER_STATUS      VARCHAR2(20) NOT NULL,   -- RELEASED | IN_PROGRESS | COMPLETED | CANCELLED (soft delete)
    LAST_UPDATE_DATE  DATE DEFAULT SYSDATE NOT NULL
);

-- The watermark column MUST be maintained on every update, and indexed, or incremental is unsafe/slow.
CREATE OR REPLACE TRIGGER ERP.TRG_PRODUCTS_LUD BEFORE INSERT OR UPDATE ON ERP.PRODUCTS
FOR EACH ROW BEGIN :NEW.LAST_UPDATE_DATE := SYSDATE; END;
/
CREATE OR REPLACE TRIGGER ERP.TRG_ORDERS_LUD BEFORE INSERT OR UPDATE ON ERP.PRODUCTION_ORDERS
FOR EACH ROW BEGIN :NEW.LAST_UPDATE_DATE := SYSDATE; END;
/
CREATE INDEX ERP.IX_PRODUCTS_LUD ON ERP.PRODUCTS (LAST_UPDATE_DATE);
CREATE INDEX ERP.IX_ORDERS_LUD   ON ERP.PRODUCTION_ORDERS (LAST_UPDATE_DATE);

-- Read-only extraction user (password in Azure Key Vault, secret: erp-oracle-password)
CREATE USER SVC_ADF_ERP IDENTIFIED BY "<set-by-dba>";
GRANT CREATE SESSION TO SVC_ADF_ERP;
GRANT SELECT ON ERP.PLANTS TO SVC_ADF_ERP;
GRANT SELECT ON ERP.PRODUCTS TO SVC_ADF_ERP;
GRANT SELECT ON ERP.PRODUCTION_ORDERS TO SVC_ADF_ERP;

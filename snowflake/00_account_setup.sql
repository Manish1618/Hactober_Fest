-- DataMind: warehouse, credit cap and database.
-- Run as ACCOUNTADMIN in Snowsight or SnowSQL. Safe to run more than once.

USE ROLE ACCOUNTADMIN;

-- Small warehouse that suspends itself after 60 seconds idle (FR-G2, NFR-02).
CREATE WAREHOUSE IF NOT EXISTS DATAMIND_WH
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  COMMENT = 'DataMind query warehouse';

-- Re-assert the settings in case the warehouse already existed with others.
ALTER WAREHOUSE DATAMIND_WH SET WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;

-- Credit cap. Adjust CREDIT_QUOTA to your trial or budget.
CREATE RESOURCE MONITOR IF NOT EXISTS DATAMIND_RM
  WITH CREDIT_QUOTA = 20
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  TRIGGERS
    ON 75 PERCENT DO NOTIFY
    ON 90 PERCENT DO SUSPEND
    ON 100 PERCENT DO SUSPEND_IMMEDIATE;

ALTER WAREHOUSE DATAMIND_WH SET RESOURCE_MONITOR = DATAMIND_RM;

-- Our own database for the OWID tables and the data-change demo.
CREATE DATABASE IF NOT EXISTS DATAMIND_DB COMMENT = 'DataMind demo data (OWID)';
CREATE SCHEMA IF NOT EXISTS DATAMIND_DB.OWID;

-- TPC-H lives in the shared SNOWFLAKE_SAMPLE_DATA database. If it is missing in your account,
-- add it from the Snowflake sample data documentation, then rerun 01_roles_and_grants.sql.

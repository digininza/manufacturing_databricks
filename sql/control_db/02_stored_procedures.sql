/* =============================================================================
   CONTROL-DB STORED PROCEDURES — called by ADF (Lookup / Stored Procedure activities)

   Lifecycle of one entity extraction:

     usp_start_ingestion_run          -> STARTED   (window frozen, queries rendered)
       (ADF: count source, Copy to _staging)
     usp_validate_ingestion_counts    -> VALIDATED  or RECON_FAILED + THROW (pipeline fails, alert)
       (ADF: publish staging -> raw, write manifest)
     usp_complete_ingestion_run       -> SUCCESS    + watermark ADVANCED (single transaction)

     usp_fail_ingestion_run           -> FAILED     (any activity error; watermark untouched)

   WHY THE LOGIC IS HERE AND NOT IN ADF EXPRESSIONS
   * one place for the rules, unit-testable with tSQLt, identical for all sources
   * the reconciliation gate is enforced by the database: no pipeline can
     "forget" to check counts before moving the watermark
   * ADF stays flat (no nested If activities, which ADF does not allow anyway)
   ============================================================================= */

CREATE OR ALTER PROCEDURE ctl.usp_log_audit
    @run_id VARCHAR(120), @entity_name VARCHAR(100), @event_type VARCHAR(50), @event_detail NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO ctl.audit_log (run_id, entity_name, event_type, event_detail) VALUES (@run_id, @entity_name, @event_type, @event_detail);
END;
GO

/* ---------------------------------------------------------------------------- batch level */
CREATE OR ALTER PROCEDURE ctl.usp_start_batch
    @batch_group VARCHAR(20), @run_date DATE, @adf_pipeline_run_id VARCHAR(100)
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO ctl.batch_run (batch_group, run_date, adf_pipeline_run_id, status) VALUES (@batch_group, @run_date, @adf_pipeline_run_id, 'RUNNING');
    SELECT CAST(SCOPE_IDENTITY() AS BIGINT) AS batch_run_id;
END;
GO

/* Returns the entities still to do for this batch/run_date.
   RESTARTABILITY: an entity that already reached SUCCESS / NO_CHANGES for the
   run_date is skipped, so re-running the master pipeline after a partial
   failure only re-processes what failed ("rerun from failure"). */
CREATE OR ALTER PROCEDURE ctl.usp_get_pending_entities
    @batch_group VARCHAR(20), @run_date DATE, @force_rerun BIT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SELECT c.entity_id, c.entity_name, c.source_system, c.source_type, c.source_object, c.cdc_capture_instance,
           c.watermark_type, c.hwm_query, c.landing_folder, c.file_pattern, c.target_container, c.target_folder,
           c.api_safety_lag_minutes, c.alert_severity, c.priority
    FROM ctl.ingestion_control c
    WHERE c.is_active = 1
      AND c.batch_group = @batch_group
      AND (   @force_rerun = 1
           OR @batch_group <> 'DAILY'
           OR NOT EXISTS (SELECT 1 FROM ctl.ingestion_run_history h
                          WHERE h.entity_id = c.entity_id AND h.load_date = @run_date AND h.status IN ('SUCCESS','NO_CHANGES')))
    ORDER BY c.priority, c.entity_name;
END;
GO

CREATE OR ALTER PROCEDURE ctl.usp_complete_batch
    @batch_run_id BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @total INT, @ok INT, @failed INT;
    SELECT @total = COUNT(*),
           @ok = SUM(CASE WHEN status IN ('SUCCESS','NO_CHANGES') THEN 1 ELSE 0 END),
           @failed = SUM(CASE WHEN status IN ('FAILED','RECON_FAILED','STARTED','VALIDATED') THEN 1 ELSE 0 END)
    FROM ctl.ingestion_run_history WHERE batch_run_id = @batch_run_id;

    UPDATE ctl.batch_run
       SET status = CASE WHEN ISNULL(@failed,0) = 0 THEN 'SUCCESS' WHEN ISNULL(@ok,0) = 0 THEN 'FAILED' ELSE 'PARTIAL_FAILURE' END,
           entities_total = ISNULL(@total,0), entities_succeeded = ISNULL(@ok,0), entities_failed = ISNULL(@failed,0), end_ts = SYSUTCDATETIME()
     WHERE batch_run_id = @batch_run_id;

    SELECT status, entities_total, entities_succeeded, entities_failed FROM ctl.batch_run WHERE batch_run_id = @batch_run_id;
END;
GO

/* ---------------------------------------------------------------------------- entity level */
/* Starts (or RESUMES) the extraction of one entity.

   IDEMPOTENCY: if the previous attempt for this entity did not finish (FAILED /
   RECON_FAILED / STARTED / VALIDATED), we REUSE its run_id and its frozen window.
   The retry therefore extracts exactly the same window into exactly the same
   raw path — overwriting partial output instead of creating a second copy. */
CREATE OR ALTER PROCEDURE ctl.usp_start_ingestion_run
    @entity_id INT,
    @batch_run_id BIGINT,
    @adf_pipeline_run_id VARCHAR(100),
    @load_date DATE,
    @candidate_window_end VARCHAR(100) = NULL   -- from hwm_query (LSN/DB) or ADF utcNow() (API/files)
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    DECLARE @entity_name VARCHAR(100), @wm_type VARCHAR(20), @last_wm VARCHAR(100), @target_folder VARCHAR(500),
            @src_tpl NVARCHAR(MAX), @cnt_tpl NVARCHAR(MAX), @run_id VARCHAR(120), @attempt INT, @ws VARCHAR(100), @we VARCHAR(100);

    BEGIN TRAN;  -- UPDLOCK on the control row serialises two concurrent triggers for the same entity
    SELECT @entity_name = entity_name, @wm_type = watermark_type, @last_wm = ISNULL(last_watermark_value, ISNULL(initial_watermark_value,'')),
           @target_folder = target_folder, @src_tpl = source_query_template, @cnt_tpl = count_query_template
    FROM ctl.ingestion_control WITH (UPDLOCK, HOLDLOCK) WHERE entity_id = @entity_id;

    SELECT TOP 1 @run_id = run_id, @attempt = attempt_no + 1, @ws = window_start, @we = window_end
    FROM ctl.ingestion_run_history
    WHERE entity_id = @entity_id AND status IN ('STARTED','VALIDATED','FAILED','RECON_FAILED')
    ORDER BY start_ts DESC;

    IF @run_id IS NOT NULL
    BEGIN
        UPDATE ctl.ingestion_run_history
           SET attempt_no = @attempt, adf_pipeline_run_id = @adf_pipeline_run_id, batch_run_id = @batch_run_id,
               status = 'STARTED', error_message = NULL, end_ts = NULL
         WHERE run_id = @run_id;
        EXEC ctl.usp_log_audit @run_id, @entity_name, 'RUN_RETRY', NULL;
    END
    ELSE
    BEGIN
        SET @attempt = 1;
        SET @ws = CASE WHEN @wm_type = 'NONE' THEN '' ELSE @last_wm END;
        SET @we = CASE WHEN @wm_type = 'NONE' THEN '' ELSE ISNULL(@candidate_window_end, @last_wm) END;
        DECLARE @seq INT = 1 + (SELECT COUNT(*) FROM ctl.ingestion_run_history WHERE entity_id = @entity_id AND load_date = @load_date);
        SET @run_id = CONCAT(@entity_name, '_', FORMAT(@load_date, 'yyyyMMdd'), '_', RIGHT('0' + CAST(@seq AS VARCHAR(3)), 2));

        INSERT INTO ctl.ingestion_run_history (run_id, entity_id, entity_name, batch_run_id, adf_pipeline_run_id, attempt_no, load_date,
                                               window_start, window_end, source_query, count_query, target_path, status)
        VALUES (@run_id, @entity_id, @entity_name, @batch_run_id, @adf_pipeline_run_id, 1, @load_date, @ws, @we,
                REPLACE(REPLACE(@src_tpl, '{window_start}', @ws), '{window_end}', @we),
                REPLACE(REPLACE(@cnt_tpl, '{window_start}', @ws), '{window_end}', @we),
                CONCAT(@target_folder, '/load_date=', FORMAT(@load_date, 'yyyy-MM-dd'), '/run_id=', @run_id), 'STARTED');
        EXEC ctl.usp_log_audit @run_id, @entity_name, 'RUN_STARTED', NULL;
    END
    COMMIT;

    SELECT h.run_id, h.attempt_no, h.window_start, h.window_end, h.source_query, h.count_query, h.target_path,
           CAST(CASE WHEN @wm_type = 'NONE' THEN 1
                     WHEN h.window_end IS NULL OR h.window_end = '' THEN 0
                     WHEN h.window_end > h.window_start THEN 1   -- fixed-width LSN hex / ISO datetime strings sort correctly
                     ELSE 0 END AS BIT) AS has_changes
    FROM ctl.ingestion_run_history h WHERE h.run_id = @run_id;
END;
GO

/* THE RECONCILIATION GATE (ADF side).
   source_count (independent COUNT on the source for the window)
     == rows_read (what Copy read) == rows_copied + rows_skipped (what Copy wrote / rejected)
   and skipped rows within the entity's tolerance.
   On mismatch: RECON_FAILED + THROW -> the ADF activity fails -> alert -> watermark NOT advanced. */
CREATE OR ALTER PROCEDURE ctl.usp_validate_ingestion_counts
    @run_id VARCHAR(120), @source_count BIGINT, @rows_read BIGINT, @rows_copied BIGINT, @rows_skipped BIGINT = 0
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @entity_name VARCHAR(100), @max_reject DECIMAL(5,2), @msg NVARCHAR(2000);
    SELECT @entity_name = h.entity_name, @max_reject = c.max_reject_pct
    FROM ctl.ingestion_run_history h JOIN ctl.ingestion_control c ON c.entity_id = h.entity_id WHERE h.run_id = @run_id;

    SET @source_count = CASE WHEN @source_count < 0 THEN @rows_read ELSE @source_count END;  -- files: the file IS the source
    SET @rows_skipped = ISNULL(@rows_skipped, 0);

    UPDATE ctl.ingestion_run_history
       SET source_count = @source_count, rows_read = @rows_read, rows_copied = @rows_copied, rows_skipped = @rows_skipped
     WHERE run_id = @run_id;

    IF @source_count <> @rows_read
        SET @msg = CONCAT('source_count (', @source_count, ') <> rows_read (', @rows_read, ')');
    ELSE IF @rows_read <> @rows_copied + @rows_skipped
        SET @msg = CONCAT('rows_read (', @rows_read, ') <> rows_copied (', @rows_copied, ') + rows_skipped (', @rows_skipped, ')');
    ELSE IF @rows_read > 0 AND (100.0 * @rows_skipped / @rows_read) > @max_reject
        SET @msg = CONCAT('rejected ', @rows_skipped, ' of ', @rows_read, ' rows exceeds tolerance ', @max_reject, '%');

    IF @msg IS NOT NULL
    BEGIN
        UPDATE ctl.ingestion_run_history SET status = 'RECON_FAILED', error_message = @msg, end_ts = SYSUTCDATETIME() WHERE run_id = @run_id;
        EXEC ctl.usp_log_audit @run_id, @entity_name, 'RECON_FAILED', @msg;
        SET @msg = CONCAT('RECONCILIATION FAILED for ', @run_id, ': ', @msg);
        THROW 50010, @msg, 1;
    END

    UPDATE ctl.ingestion_run_history SET status = 'VALIDATED' WHERE run_id = @run_id;
    EXEC ctl.usp_log_audit @run_id, @entity_name, 'COUNTS_VALIDATED', NULL;
END;
GO

/* Advances the watermark. ONLY place in the whole platform that does so for ADF sources.
   Requires the run to be VALIDATED (or NO_CHANGES). Single transaction. Safe to call twice. */
CREATE OR ALTER PROCEDURE ctl.usp_complete_ingestion_run
    @run_id VARCHAR(120), @no_changes BIT = 0
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    DECLARE @entity_id INT, @entity_name VARCHAR(100), @status VARCHAR(20), @we VARCHAR(100), @old VARCHAR(100), @wm_type VARCHAR(20);

    SELECT @entity_id = h.entity_id, @entity_name = h.entity_name, @status = h.status, @we = h.window_end,
           @old = c.last_watermark_value, @wm_type = c.watermark_type
    FROM ctl.ingestion_run_history h JOIN ctl.ingestion_control c ON c.entity_id = h.entity_id WHERE h.run_id = @run_id;

    IF @status IN ('SUCCESS','NO_CHANGES') RETURN;  -- idempotent
    IF @no_changes = 0 AND @status <> 'VALIDATED'
        THROW 50011, 'Cannot complete a run that has not passed count validation', 1;

    BEGIN TRAN;
        IF @no_changes = 0 AND @wm_type <> 'NONE'
        BEGIN
            UPDATE ctl.ingestion_control SET last_watermark_value = @we, updated_ts = SYSUTCDATETIME() WHERE entity_id = @entity_id;
            INSERT INTO ctl.watermark_history (entity_id, old_value, new_value, run_id, change_type) VALUES (@entity_id, @old, @we, @run_id, 'ADVANCE');
        END
        UPDATE ctl.ingestion_run_history
           SET status = CASE WHEN @no_changes = 1 THEN 'NO_CHANGES' ELSE 'SUCCESS' END,
               source_count = CASE WHEN @no_changes = 1 THEN 0 ELSE source_count END,
               end_ts = SYSUTCDATETIME()
         WHERE run_id = @run_id;
        EXEC ctl.usp_log_audit @run_id, @entity_name, 'WATERMARK_ADVANCED', NULL;
    COMMIT;
END;
GO

CREATE OR ALTER PROCEDURE ctl.usp_fail_ingestion_run
    @run_id VARCHAR(120), @error_message NVARCHAR(4000)
AS
BEGIN
    SET NOCOUNT ON;
    UPDATE ctl.ingestion_run_history
       SET status = CASE WHEN status = 'RECON_FAILED' THEN status ELSE 'FAILED' END,
           error_message = COALESCE(error_message, LEFT(@error_message, 4000)), end_ts = SYSUTCDATETIME()
     WHERE run_id = @run_id AND status NOT IN ('SUCCESS','NO_CHANGES');
    DECLARE @entity_name VARCHAR(100) = (SELECT entity_name FROM ctl.ingestion_run_history WHERE run_id = @run_id);
    EXEC ctl.usp_log_audit @run_id, @entity_name, 'RUN_FAILED', @error_message;
END;
GO

/* Operations only (never called by a pipeline): replay history or recover from a CDC LSN gap. */
CREATE OR ALTER PROCEDURE ctl.usp_reset_watermark
    @entity_name VARCHAR(100), @new_value VARCHAR(100) = NULL, @reason NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON; SET XACT_ABORT ON;
    IF @reason IS NULL OR LEN(@reason) < 10 THROW 50020, 'A meaningful reason (ticket number) is required to reset a watermark', 1;
    DECLARE @entity_id INT, @old VARCHAR(100), @initial VARCHAR(100);
    SELECT @entity_id = entity_id, @old = last_watermark_value, @initial = initial_watermark_value FROM ctl.ingestion_control WHERE entity_name = @entity_name;
    BEGIN TRAN;
        UPDATE ctl.ingestion_control SET last_watermark_value = COALESCE(@new_value, @initial), updated_ts = SYSUTCDATETIME() WHERE entity_id = @entity_id;
        INSERT INTO ctl.watermark_history (entity_id, old_value, new_value, change_type, reason) VALUES (@entity_id, @old, COALESCE(@new_value, @initial), 'MANUAL_RESET', @reason);
        -- abandon any half-finished attempt so the next run starts a fresh window from the reset point
        UPDATE ctl.ingestion_run_history SET status = 'FAILED', error_message = CONCAT('superseded by watermark reset: ', @reason)
         WHERE entity_id = @entity_id AND status IN ('STARTED','VALIDATED','RECON_FAILED');
        UPDATE ctl.ingestion_run_history SET status = 'SUPERSEDED' WHERE entity_id = @entity_id AND status = 'FAILED' AND error_message LIKE 'superseded by watermark reset%';
    COMMIT;
    EXEC ctl.usp_log_audit NULL, @entity_name, 'WATERMARK_RESET', @reason;
END;
GO

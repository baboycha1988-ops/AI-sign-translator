-- Run AFTER 0001_initial.sql:
--   psql -v ON_ERROR_STOP=1 -f db/tests/constraints.sql "$DATABASE_URL"
-- Every test expects a specific database error. All changes are rolled back.
BEGIN;

CREATE FUNCTION pg_temp.expect_error(sql text, expected_state text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    EXECUTE sql;
  EXCEPTION WHEN OTHERS THEN
    IF SQLSTATE = expected_state THEN
      RETURN;
    END IF;
    RAISE EXCEPTION 'expected SQLSTATE %, got % (%) for: %', expected_state, SQLSTATE, SQLERRM, sql;
  END;
  RAISE EXCEPTION 'expected error % but statement succeeded: %', expected_state, sql;
END $$;

DO $$
DECLARE
  u uuid; sl smallint; ds uuid; sg uuid; sgr uuid; sig uuid;
  con_revoked uuid; con_live uuid; m1 uuid; m2 uuid; n int;
BEGIN
  SELECT id INTO sl FROM sign_languages WHERE code = 'tajik-sl';

  -- T1: blocked user must have a reason; duplicate e-mail rejected
  PERFORM pg_temp.expect_error($q$INSERT INTO users (email, status) VALUES ('b@x.tj','blocked')$q$, '23514');
  INSERT INTO users (email) VALUES ('t1@x.tj') RETURNING id INTO u;
  PERFORM pg_temp.expect_error($q$INSERT INTO users (email) VALUES ('t1@x.tj')$q$, '23505');

  -- T2: sign language without validated dataset cannot get a current model
  INSERT INTO ai_models (name, version, sign_language_id, status, artifact_key, artifact_sha256, gate_passed_at)
  VALUES ('m', 'v1', sl, 'published', 'k1', 'h1', now()) RETURNING id INTO m1;
  PERFORM pg_temp.expect_error(format('UPDATE ai_models SET is_current = true WHERE id = %L', m1), '23514');

  -- T3: after validation, publishing works; gate is mandatory
  UPDATE sign_languages SET evidence_status = 'validated' WHERE id = sl;
  INSERT INTO ai_models (name, version, sign_language_id, status, artifact_key, artifact_sha256)
  VALUES ('m', 'v2-nogate', sl, 'published', 'k2', 'h2') RETURNING id INTO m2;
  PERFORM pg_temp.expect_error(format('UPDATE ai_models SET is_current = true WHERE id = %L', m2), '23514');
  PERFORM set_current_model(m1, u);
  SELECT count(*) INTO n FROM ai_models WHERE is_current;
  IF n <> 1 THEN RAISE EXCEPTION 'expected exactly one current model, got %', n; END IF;
  -- rollback of current model to a different published model
  UPDATE ai_models SET gate_passed_at = now() WHERE id = m2;
  PERFORM set_current_model(m2, u);
  PERFORM set_current_model(m1, u);
  IF NOT (SELECT is_current FROM ai_models WHERE id = m1) THEN RAISE EXCEPTION 'rollback failed'; END IF;
  -- two current models for one language is impossible
  PERFORM pg_temp.expect_error(format('UPDATE ai_models SET is_current = true WHERE id = %L', m2), '23505');

  -- T4: audit log is append-only; set_current_model wrote entries
  SELECT count(*) INTO n FROM audit_logs WHERE action = 'model.set_current';
  IF n < 3 THEN RAISE EXCEPTION 'expected audit entries, got %', n; END IF;
  PERFORM pg_temp.expect_error($q$UPDATE audit_logs SET action = 'x'$q$, '23001');
  PERFORM pg_temp.expect_error($q$DELETE FROM audit_logs$q$, '23001');

  -- T5: proposal state machine
  INSERT INTO sign_proposals (proposer_id, sign_language_id, gloss) VALUES (u, sl, 'test') RETURNING id INTO sg;
  PERFORM pg_temp.expect_error(format('UPDATE sign_proposals SET status = %L WHERE id = %L', 'published', sg), '23514');
  UPDATE sign_proposals SET status = 'submitted' WHERE id = sg;
  UPDATE sign_proposals SET status = 'in_review' WHERE id = sg;
  PERFORM pg_temp.expect_error(format('UPDATE sign_proposals SET status = %L WHERE id = %L', 'rejected', sg), '23514'); -- needs review_note

  -- T6: samples require a live consent and the same signer
  INSERT INTO sign_dictionary (sign_language_id, gloss) VALUES (sl, 'hello') RETURNING id INTO sgr;
  INSERT INTO datasets (name, sign_language_id) VALUES ('t', sl) RETURNING id INTO ds;
  INSERT INTO signers (pseudonym) VALUES ('s-001') RETURNING id INTO sig;
  INSERT INTO consents (signer_id, scope, text_version) VALUES (sig, 'dataset_training', 'v1') RETURNING id INTO con_revoked;
  UPDATE consents SET revoked_at = now() WHERE id = con_revoked;
  PERFORM pg_temp.expect_error(format(
    'INSERT INTO dataset_samples (dataset_id, sign_id, signer_id, consent_id, source, landmarks_key) VALUES (%L,%L,%L,%L,%L,%L)',
    ds, sgr, sig, con_revoked, 'camera', 'lm1'), '23514');

  -- a live consent works
  INSERT INTO consents (signer_id, scope, text_version) VALUES (sig, 'dataset_training', 'v1') RETURNING id INTO con_live;
  INSERT INTO dataset_samples (dataset_id, sign_id, signer_id, consent_id, source, landmarks_key)
  VALUES (ds, sgr, sig, con_live, 'camera', 'lm2');
  -- a consent of another signer cannot be reused (composite foreign key)
  INSERT INTO signers (pseudonym) VALUES ('s-001b') RETURNING id INTO sig;
  PERFORM pg_temp.expect_error(format(
    'INSERT INTO dataset_samples (dataset_id, sign_id, signer_id, consent_id, source, landmarks_key) VALUES (%L,%L,%L,%L,%L,%L)',
    ds, sgr, sig, con_live, 'camera', 'lm3'), '23503');
END $$;

-- Second block: dataset versions (uses fresh data to keep blocks readable)
DO $$
DECLARE
  sl smallint; ds uuid; sgr uuid; sig uuid; con uuid; s_ok uuid; s_pending uuid; s_ok2 uuid;
  v_open uuid; v_frozen uuid; n int;
BEGIN
  SELECT id INTO sl FROM sign_languages WHERE code = 'tajik-sl';
  INSERT INTO sign_dictionary (sign_language_id, gloss) VALUES (sl, 'water') RETURNING id INTO sgr;
  INSERT INTO datasets (name, sign_language_id) VALUES ('t2', sl) RETURNING id INTO ds;
  INSERT INTO signers (pseudonym) VALUES ('s-002') RETURNING id INTO sig;
  INSERT INTO consents (signer_id, scope, text_version) VALUES (sig, 'dataset_training', 'v1') RETURNING id INTO con;

  INSERT INTO dataset_samples (dataset_id, sign_id, signer_id, consent_id, source, landmarks_key,
                               qc_status, label_status, verified_at)
  VALUES (ds, sgr, sig, con, 'camera', 'a', 'passed', 'verified', now()) RETURNING id INTO s_ok;
  INSERT INTO dataset_samples (dataset_id, sign_id, signer_id, consent_id, source, landmarks_key,
                               qc_status, label_status, verified_at)
  VALUES (ds, sgr, sig, con, 'camera', 'b', 'passed', 'verified', now()) RETURNING id INTO s_ok2;
  INSERT INTO dataset_samples (dataset_id, sign_id, signer_id, consent_id, source, landmarks_key)
  VALUES (ds, sgr, sig, con, 'camera', 'c') RETURNING id INTO s_pending;

  INSERT INTO dataset_versions (dataset_id, version_no) VALUES (ds, 1) RETURNING id INTO v_open;
  INSERT INTO dataset_versions (dataset_id, version_no, frozen_at) VALUES (ds, 2, now()) RETURNING id INTO v_frozen;

  -- unverified sample cannot enter a version
  PERFORM pg_temp.expect_error(format(
    'INSERT INTO dataset_version_samples VALUES (%L,%L,%L)', v_open, s_pending, 'train'), '23514');
  -- frozen version is immutable
  PERFORM pg_temp.expect_error(format(
    'INSERT INTO dataset_version_samples VALUES (%L,%L,%L)', v_frozen, s_ok, 'train'), '23514');
  -- verified sample enters an open version
  INSERT INTO dataset_version_samples VALUES (v_open, s_ok, 'train');

  -- signer leakage view detects the same signer in two splits
  INSERT INTO dataset_version_samples VALUES (v_open, s_ok2, 'test');
  SELECT count(*) INTO n FROM v_split_signer_leakage WHERE version_id = v_open;
  IF n <> 1 THEN RAISE EXCEPTION 'leakage view should flag the signer, got %', n; END IF;

  -- consent revocation soft-deletes samples and queues file deletion
  PERFORM revoke_consent(con, NULL);
  SELECT count(*) INTO n FROM dataset_samples WHERE consent_id = con AND deleted_at IS NULL;
  IF n <> 0 THEN RAISE EXCEPTION 'samples must be soft-deleted after revoke, got %', n; END IF;
  SELECT count(*) INTO n FROM jobs WHERE kind = 'delete_files';
  IF n < 3 THEN RAISE EXCEPTION 'expected delete_files jobs, got %', n; END IF;
END $$;

-- Third block: privacy + subscriptions
DO $$
DECLARE u uuid; p smallint; n int;
BEGIN
  INSERT INTO users (email, status, password_hash) VALUES ('t3@x.tj', 'active', 'argon2id$x') RETURNING id INTO u;
  INSERT INTO profiles (user_id) VALUES (u);
  INSERT INTO translation_history (user_id, mode, text_result, language_code) VALUES (u, 'sign_to_text', 'salom', 'tg');
  SELECT id INTO p FROM plans WHERE code = 'free';
  INSERT INTO subscriptions (user_id, plan_id, status) VALUES (u, p, 'active');
  PERFORM pg_temp.expect_error(format(
    'INSERT INTO subscriptions (user_id, plan_id, status) VALUES (%L,%L,%L)', u, p, 'active'), '23505');

  PERFORM anonymize_user(u, NULL);
  SELECT count(*) INTO n FROM translation_history WHERE user_id = u;
  IF n <> 0 THEN RAISE EXCEPTION 'history must be erased'; END IF;
  IF (SELECT email FROM users WHERE id = u) NOT LIKE 'deleted-%@deleted.invalid' THEN
    RAISE EXCEPTION 'email must be anonymized';
  END IF;
END $$;

ROLLBACK;
\echo ALL DB TESTS PASSED

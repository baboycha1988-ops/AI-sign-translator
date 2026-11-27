-- =====================================================================
-- AI Sign Translator — migration 0001: initial schema
-- Target: PostgreSQL 13+ (gen_random_uuid() is built in since 13).
-- Conventions:
--   * enums are TEXT + CHECK (easier to migrate than CREATE TYPE)
--   * high-volume tables use bigint IDENTITY, others uuid
--   * NO video of ordinary users is ever stored here; only dataset samples
--     with an explicit, non-revoked consent (see consents / dataset_samples)
--   * NO secrets and NO card data are stored in this database
-- =====================================================================
BEGIN;

-- ---------- helpers ---------------------------------------------------
CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$;

-- ---------- languages (UI / result languages; easy to extend) ---------
CREATE TABLE languages (
  code         text PRIMARY KEY CHECK (code ~ '^[a-z]{2,3}(-[A-Za-z0-9]+)*$'),
  name         text NOT NULL,
  native_name  text NOT NULL,
  is_active    boolean NOT NULL DEFAULT true
);
INSERT INTO languages (code, name, native_name) VALUES
  ('tg', 'Tajik',   'Тоҷикӣ'),
  ('ru', 'Russian', 'Русский'),
  ('en', 'English', 'English');

-- ---------- roles & permissions --------------------------------------
CREATE TABLE roles (
  id          smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code        text NOT NULL UNIQUE CHECK (code ~ '^[a-z_]+$'),
  name        text NOT NULL,
  description text
);
CREATE TABLE permissions (
  id          smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code        text NOT NULL UNIQUE CHECK (code ~ '^[a-z_.]+$'),
  description text NOT NULL
);
CREATE TABLE role_permissions (
  role_id       smallint NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
  permission_id smallint NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_id)
);

INSERT INTO roles (code, name, description) VALUES
  ('user',        'User',        'Regular user of the translator'),
  ('contributor', 'Contributor', 'User who accepted dataset-contribution terms'),
  ('annotator',   'Annotator',   'Reviews labels of samples that have valid consent'),
  ('admin',       'Admin',       'Operates dictionary, users, models, datasets'),
  ('superadmin',  'Superadmin',  'System settings and role management');

-- NOTE: there is intentionally NO permission that grants access to private
-- user videos. Ordinary users' videos are not stored at all.
INSERT INTO permissions (code, description) VALUES
  ('translate.use',          'Use the translator'),
  ('history.own',            'Read and manage own history'),
  ('signs.propose',          'Propose new signs'),
  ('samples.upload_own',     'Upload or record dataset samples with consent'),
  ('samples.review_consented','Review samples that have valid consent'),
  ('signs.review',           'Approve, reject, merge proposed signs'),
  ('users.manage',           'View and block users, manage roles of non-admins'),
  ('reports.manage',         'Handle error reports'),
  ('dataset.manage',         'Manage datasets and dataset versions'),
  ('models.manage',          'Upload, train and compare models'),
  ('models.publish',         'Publish models and roll back'),
  ('audit.read',             'Read the audit log'),
  ('system.manage',          'Change system settings'),
  ('roles.manage',           'Grant admin roles');

INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM (VALUES
  ('user','translate.use'), ('user','history.own'), ('user','signs.propose'),
  ('contributor','samples.upload_own'),
  ('annotator','samples.review_consented'),
  ('admin','signs.review'), ('admin','users.manage'), ('admin','reports.manage'),
  ('admin','dataset.manage'), ('admin','models.manage'), ('admin','models.publish'),
  ('admin','audit.read'),
  ('superadmin','system.manage'), ('superadmin','roles.manage')
) AS m(role_code, perm_code)
JOIN roles r       ON r.code = m.role_code
JOIN permissions p ON p.code = m.perm_code;

-- ---------- users & profiles -----------------------------------------
CREATE TABLE users (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email             text NOT NULL UNIQUE
                      CHECK (email = lower(email) AND position('@' in email) > 1),
  password_hash     text,                       -- Argon2id hash, never plaintext
  status            text NOT NULL DEFAULT 'pending_verification'
                      CHECK (status IN ('pending_verification','active','blocked','deleted')),
  email_verified_at timestamptz,
  blocked_reason    text,
  last_login_at     timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  deleted_at        timestamptz,
  CHECK (status <> 'blocked' OR blocked_reason IS NOT NULL),
  CHECK (status <> 'active'  OR password_hash IS NOT NULL)
);
CREATE TRIGGER users_updated BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE user_roles (
  user_id    uuid     NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id    smallint NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
  granted_by uuid REFERENCES users(id) ON DELETE SET NULL,
  granted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, role_id)
);
CREATE INDEX user_roles_role_idx ON user_roles (role_id);

CREATE TABLE refresh_tokens (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  family_id   uuid NOT NULL,                    -- rotation family; reuse => revoke family
  token_hash  text NOT NULL UNIQUE,             -- hash only, never the token
  expires_at  timestamptz NOT NULL,
  revoked_at  timestamptz,
  replaced_by uuid REFERENCES refresh_tokens(id) ON DELETE SET NULL,
  user_agent  text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX refresh_tokens_user_idx   ON refresh_tokens (user_id);
CREATE INDEX refresh_tokens_family_idx ON refresh_tokens (family_id);

CREATE TABLE profiles (
  user_id          uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  display_name     text CHECK (display_name IS NULL OR char_length(display_name) BETWEEN 1 AND 80),
  avatar_key       text,
  ui_language      text NOT NULL DEFAULT 'tg' REFERENCES languages(code),
  result_language  text NOT NULL DEFAULT 'tg' REFERENCES languages(code),
  theme            text NOT NULL DEFAULT 'system' CHECK (theme IN ('light','dark','system')),
  subtitle_size    smallint NOT NULL DEFAULT 32 CHECK (subtitle_size BETWEEN 12 AND 96),
  camera_settings  jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(camera_settings) = 'object'),
  privacy_settings jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(privacy_settings) = 'object'),
  save_history     boolean NOT NULL DEFAULT true,
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER profiles_updated BEFORE UPDATE ON profiles
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------- plans & subscriptions (no card data, ever) ---------------
CREATE TABLE plans (
  id             smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code           text NOT NULL UNIQUE,
  name           text NOT NULL,
  price_minor    integer CHECK (price_minor IS NULL OR price_minor >= 0), -- NULL = not decided yet
  currency       char(3),
  billing_period text NOT NULL DEFAULT 'none' CHECK (billing_period IN ('none','month','year')),
  features       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(features) = 'object'),
  is_active      boolean NOT NULL DEFAULT false
);
-- Core translation stays free. Premium price is intentionally undecided.
INSERT INTO plans (code, name, price_minor, billing_period, features, is_active) VALUES
  ('free',    'Free',    0,    'none', '{"core_translation": true}', true),
  ('premium', 'Premium', NULL, 'month','{"extended_history": true, "export": true}', false);

CREATE TABLE subscriptions (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  plan_id              smallint NOT NULL REFERENCES plans(id),
  status               text NOT NULL CHECK (status IN ('active','trialing','past_due','canceled','expired')),
  provider             text,                      -- pluggable payment provider code
  provider_ref         text,                      -- provider's subscription id (opaque)
  current_period_start timestamptz,
  current_period_end   timestamptz,
  cancel_at_period_end boolean NOT NULL DEFAULT false,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX subscriptions_one_live_per_user
  ON subscriptions (user_id) WHERE status IN ('active','trialing','past_due');
CREATE UNIQUE INDEX subscriptions_provider_ref_uq
  ON subscriptions (provider, provider_ref) WHERE provider_ref IS NOT NULL;
CREATE TRIGGER subscriptions_updated BEFORE UPDATE ON subscriptions
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------- sign languages (honesty gate) ----------------------------
CREATE TABLE sign_languages (
  id                 smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  code               text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9-]+$'),
  name               text NOT NULL,
  evidence_status    text NOT NULL DEFAULT 'no_verified_dataset'
                       CHECK (evidence_status IN ('no_verified_dataset','dataset_in_progress','validated')),
  -- Recognition may be switched on only for a validated language.
  recognition_enabled boolean GENERATED ALWAYS AS (evidence_status = 'validated') STORED,
  notes              text
);
INSERT INTO sign_languages (code, name, notes) VALUES
  ('tajik-sl', 'Tajik Sign Language',
   'Not supported until a dataset is collected and validated by native signers and experts.');

-- ---------- dictionary ------------------------------------------------
CREATE TABLE sign_categories (
  id         smallint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  parent_id  smallint REFERENCES sign_categories(id) ON DELETE SET NULL,
  code       text NOT NULL UNIQUE CHECK (code ~ '^[a-z0-9_]+$'),
  names      jsonb NOT NULL CHECK (jsonb_typeof(names) = 'object'),   -- {"tg": "...", "ru": "...", "en": "..."}
  sort_order integer NOT NULL DEFAULT 0
);

CREATE TABLE sign_dictionary (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  sign_language_id    smallint NOT NULL REFERENCES sign_languages(id),
  category_id         smallint REFERENCES sign_categories(id) ON DELETE SET NULL,
  gloss               text NOT NULL CHECK (char_length(gloss) BETWEEN 1 AND 120),
  description         text,
  reference_video_key text,
  status              text NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft','approved','active','deprecated','merged')),
  merged_into_id      uuid REFERENCES sign_dictionary(id) ON DELETE RESTRICT,
  created_by          uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (sign_language_id, gloss),
  CHECK ((status = 'merged') = (merged_into_id IS NOT NULL)),
  CHECK (merged_into_id IS NULL OR merged_into_id <> id)
);
CREATE INDEX sign_dictionary_category_idx ON sign_dictionary (category_id);
CREATE INDEX sign_dictionary_status_idx   ON sign_dictionary (status);
CREATE TRIGGER sign_dictionary_updated BEFORE UPDATE ON sign_dictionary
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE sign_translations (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  sign_id       uuid NOT NULL REFERENCES sign_dictionary(id) ON DELETE CASCADE,
  language_code text NOT NULL REFERENCES languages(code),
  label         text NOT NULL CHECK (char_length(label) BETWEEN 1 AND 200),
  meaning       text,
  UNIQUE (sign_id, language_code, label)
);
CREATE INDEX sign_translations_lookup_idx ON sign_translations (language_code, lower(label));

-- ---------- consents & signers (dataset participants) ---------------
CREATE TABLE signers (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          uuid UNIQUE REFERENCES users(id) ON DELETE SET NULL,
  pseudonym        text NOT NULL UNIQUE,        -- shown to reviewers instead of a name
  is_native_signer boolean,                     -- optional; minimal data on purpose
  region           text,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE consents (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  signer_id    uuid NOT NULL REFERENCES signers(id) ON DELETE RESTRICT,
  scope        text NOT NULL CHECK (scope IN ('dataset_training','analytics')),
  text_version text NOT NULL,                   -- version of the consent text shown
  granted_at   timestamptz NOT NULL DEFAULT now(),
  revoked_at   timestamptz,
  UNIQUE (id, signer_id)
);
CREATE INDEX consents_signer_idx ON consents (signer_id);

-- ---------- sign proposals (module 20) -------------------------------
CREATE TABLE sign_proposals (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  proposer_id         uuid REFERENCES users(id) ON DELETE SET NULL,
  sign_language_id    smallint NOT NULL REFERENCES sign_languages(id),
  category_id         smallint REFERENCES sign_categories(id) ON DELETE SET NULL,
  gloss               text NOT NULL CHECK (char_length(gloss) BETWEEN 1 AND 120),
  description         text,
  translations        jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(translations) = 'array'),
  status              text NOT NULL DEFAULT 'draft' CHECK (status IN (
                        'draft','submitted','in_review','approved','rejected','merged_duplicate',
                        'collecting_samples','ready_for_training','in_training','evaluating',
                        'published','failed')),
  reviewer_id         uuid REFERENCES users(id) ON DELETE SET NULL,
  review_note         text,
  duplicate_of_sign_id uuid REFERENCES sign_dictionary(id) ON DELETE SET NULL,
  resulting_sign_id   uuid REFERENCES sign_dictionary(id) ON DELETE SET NULL,
  submitted_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  CHECK (status <> 'rejected' OR review_note IS NOT NULL),
  CHECK (status <> 'merged_duplicate' OR duplicate_of_sign_id IS NOT NULL),
  CHECK (status NOT IN ('approved','collecting_samples','ready_for_training',
                        'in_training','evaluating','published')
         OR resulting_sign_id IS NOT NULL)
);
CREATE INDEX sign_proposals_status_idx   ON sign_proposals (status);
CREATE INDEX sign_proposals_proposer_idx ON sign_proposals (proposer_id);
CREATE TRIGGER sign_proposals_updated BEFORE UPDATE ON sign_proposals
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Enforces the proposal state machine at the database level.
CREATE FUNCTION enforce_proposal_transition() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE ok boolean;
BEGIN
  IF NEW.status = OLD.status THEN
    RETURN NEW;
  END IF;
  ok := CASE OLD.status
    WHEN 'draft'              THEN NEW.status IN ('submitted')
    WHEN 'submitted'          THEN NEW.status IN ('in_review','draft')
    WHEN 'in_review'          THEN NEW.status IN ('approved','rejected','merged_duplicate')
    WHEN 'approved'           THEN NEW.status IN ('collecting_samples')
    WHEN 'collecting_samples' THEN NEW.status IN ('ready_for_training')
    WHEN 'ready_for_training' THEN NEW.status IN ('in_training','collecting_samples')
    WHEN 'in_training'        THEN NEW.status IN ('evaluating','failed')
    WHEN 'evaluating'         THEN NEW.status IN ('published','failed')
    WHEN 'failed'             THEN NEW.status IN ('collecting_samples','ready_for_training')
    WHEN 'rejected'           THEN NEW.status IN ('draft')
    ELSE false
  END;
  IF NOT ok THEN
    RAISE EXCEPTION 'invalid proposal transition % -> %', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER sign_proposals_transition BEFORE UPDATE OF status ON sign_proposals
  FOR EACH ROW EXECUTE FUNCTION enforce_proposal_transition();

-- ---------- datasets --------------------------------------------------
CREATE TABLE datasets (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name             text NOT NULL UNIQUE,
  sign_language_id smallint NOT NULL REFERENCES sign_languages(id),
  description      text,
  license_note     text,
  created_by       uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE dataset_samples (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id          uuid NOT NULL REFERENCES datasets(id) ON DELETE RESTRICT,
  sign_id             uuid REFERENCES sign_dictionary(id) ON DELETE RESTRICT,
  proposal_id         uuid REFERENCES sign_proposals(id) ON DELETE SET NULL,
  signer_id           uuid NOT NULL,
  consent_id          uuid NOT NULL,
  source              text NOT NULL CHECK (source IN ('camera','upload')),
  video_key           text,                     -- may be NULL once only landmarks are kept
  landmarks_key       text,
  video_sha256        text UNIQUE,
  duration_ms         integer CHECK (duration_ms > 0),
  fps                 real CHECK (fps > 0),
  hands_visible_ratio real CHECK (hands_visible_ratio BETWEEN 0 AND 1),
  lighting_score      real CHECK (lighting_score BETWEEN 0 AND 1),
  conditions          jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(conditions) = 'object'),
  qc_status           text NOT NULL DEFAULT 'pending' CHECK (qc_status IN ('pending','passed','failed')),
  qc_report           jsonb,
  label_status        text NOT NULL DEFAULT 'unlabeled'
                        CHECK (label_status IN ('unlabeled','labeled','verified','rejected')),
  labeled_by          uuid REFERENCES users(id) ON DELETE SET NULL,
  verified_by         uuid REFERENCES users(id) ON DELETE SET NULL,
  verified_at         timestamptz,
  created_by          uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  deleted_at          timestamptz,
  FOREIGN KEY (consent_id, signer_id) REFERENCES consents (id, signer_id),
  CHECK (sign_id IS NOT NULL OR proposal_id IS NOT NULL),
  CHECK (video_key IS NOT NULL OR landmarks_key IS NOT NULL),
  CHECK (label_status <> 'verified' OR (verified_at IS NOT NULL AND sign_id IS NOT NULL))
);
CREATE INDEX dataset_samples_sign_idx     ON dataset_samples (sign_id, qc_status);
CREATE INDEX dataset_samples_signer_idx   ON dataset_samples (signer_id);
CREATE INDEX dataset_samples_proposal_idx ON dataset_samples (proposal_id);
CREATE INDEX dataset_samples_dataset_idx  ON dataset_samples (dataset_id);

-- A sample can only be created under a live, matching-scope consent.
CREATE FUNCTION guard_sample_consent() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM consents
                 WHERE id = NEW.consent_id AND signer_id = NEW.signer_id
                   AND scope = 'dataset_training' AND revoked_at IS NULL) THEN
    RAISE EXCEPTION 'sample requires a live dataset_training consent'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER dataset_samples_consent BEFORE INSERT ON dataset_samples
  FOR EACH ROW EXECUTE FUNCTION guard_sample_consent();

CREATE TABLE dataset_versions (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id  uuid NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  version_no  integer NOT NULL CHECK (version_no > 0),
  frozen_at   timestamptz,                      -- once set, membership is immutable
  manifest_key text,
  notes       text,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (dataset_id, version_no)
);

CREATE TABLE dataset_version_samples (
  version_id uuid NOT NULL REFERENCES dataset_versions(id) ON DELETE CASCADE,
  sample_id  uuid NOT NULL REFERENCES dataset_samples(id) ON DELETE RESTRICT,
  split      text NOT NULL CHECK (split IN ('train','val','test')),
  PRIMARY KEY (version_id, sample_id)
);
CREATE INDEX dataset_version_samples_sample_idx ON dataset_version_samples (sample_id);

CREATE FUNCTION guard_version_membership() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_frozen timestamptz;
  s dataset_samples%ROWTYPE;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT frozen_at INTO v_frozen FROM dataset_versions WHERE id = OLD.version_id;
    IF v_frozen IS NOT NULL THEN
      RAISE EXCEPTION 'dataset version % is frozen', OLD.version_id
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
  END IF;

  SELECT frozen_at INTO v_frozen FROM dataset_versions WHERE id = NEW.version_id;
  IF v_frozen IS NOT NULL THEN
    RAISE EXCEPTION 'dataset version % is frozen', NEW.version_id
      USING ERRCODE = 'check_violation';
  END IF;

  SELECT * INTO s FROM dataset_samples WHERE id = NEW.sample_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'sample % not found', NEW.sample_id USING ERRCODE = 'check_violation';
  END IF;
  IF s.qc_status <> 'passed' OR s.label_status <> 'verified'
     OR s.deleted_at IS NOT NULL OR s.sign_id IS NULL THEN
    RAISE EXCEPTION 'only QC-passed, verified, non-deleted labeled samples may enter a version'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER dataset_version_samples_guard
  BEFORE INSERT OR UPDATE OR DELETE ON dataset_version_samples
  FOR EACH ROW EXECUTE FUNCTION guard_version_membership();

-- Scientific hygiene: the same signer must never appear in two splits of one version.
CREATE VIEW v_split_signer_leakage AS
SELECT dvs.version_id, s.signer_id, count(DISTINCT dvs.split) AS splits_count
FROM dataset_version_samples dvs
JOIN dataset_samples s ON s.id = dvs.sample_id
GROUP BY dvs.version_id, s.signer_id
HAVING count(DISTINCT dvs.split) > 1;

-- ---------- AI models -------------------------------------------------
CREATE TABLE ai_models (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name                text NOT NULL,
  version             text NOT NULL,
  sign_language_id    smallint NOT NULL REFERENCES sign_languages(id),
  status              text NOT NULL DEFAULT 'training'
                        CHECK (status IN ('training','evaluating','candidate','published','archived','failed')),
  architecture        text,
  hyperparams         jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(hyperparams) = 'object'),
  artifact_key        text,                     -- ONNX file in object storage
  artifact_sha256     text,
  dataset_version_id  uuid REFERENCES dataset_versions(id) ON DELETE SET NULL,
  parent_model_id     uuid REFERENCES ai_models(id) ON DELETE SET NULL,
  train_samples_count integer CHECK (train_samples_count >= 0),
  trained_at          timestamptz,
  gate_passed_at      timestamptz,              -- set only by the verification procedure
  gate_report         jsonb,                    -- incl. calibrated rejection thresholds
  is_current          boolean NOT NULL DEFAULT false,
  published_by        uuid REFERENCES users(id) ON DELETE SET NULL,
  published_at        timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (name, version),
  CHECK (NOT is_current OR (status = 'published' AND gate_passed_at IS NOT NULL
                            AND artifact_key IS NOT NULL AND artifact_sha256 IS NOT NULL))
);
CREATE UNIQUE INDEX ai_models_one_current_per_language
  ON ai_models (sign_language_id) WHERE is_current;
CREATE INDEX ai_models_status_idx ON ai_models (status);

-- A model may be current only for a sign language that has a validated dataset.
CREATE FUNCTION guard_current_model() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.is_current AND NOT EXISTS (
       SELECT 1 FROM sign_languages WHERE id = NEW.sign_language_id AND recognition_enabled) THEN
    RAISE EXCEPTION 'sign language % has no validated dataset; model cannot be current',
      NEW.sign_language_id USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ai_models_current_guard BEFORE INSERT OR UPDATE ON ai_models
  FOR EACH ROW EXECUTE FUNCTION guard_current_model();

CREATE TABLE model_signs (
  model_id    uuid NOT NULL REFERENCES ai_models(id) ON DELETE CASCADE,
  sign_id     uuid NOT NULL REFERENCES sign_dictionary(id) ON DELETE RESTRICT,
  class_index integer NOT NULL CHECK (class_index >= 0),
  PRIMARY KEY (model_id, sign_id),
  UNIQUE (model_id, class_index)
);

CREATE TABLE model_metrics (
  id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  model_id               uuid NOT NULL REFERENCES ai_models(id) ON DELETE CASCADE,
  eval_dataset_version_id uuid REFERENCES dataset_versions(id) ON DELETE SET NULL,
  split                  text NOT NULL CHECK (split IN ('val','test','external')),
  metric                 text NOT NULL CHECK (metric ~ '^[a-z0-9_]+$'),  -- top1_accuracy, f1_macro, wer, latency_ms_p95 ...
  value                  double precision NOT NULL,
  sign_id                uuid REFERENCES sign_dictionary(id) ON DELETE SET NULL, -- NULL = overall
  device_profile         text,                  -- e.g. 'desktop-chrome', 'low-end-android'
  created_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX model_metrics_model_idx ON model_metrics (model_id, metric);

CREATE VIEW v_model_added_signs AS
SELECT m.id AS model_id, ms.sign_id
FROM ai_models m
JOIN model_signs ms ON ms.model_id = m.id
WHERE m.parent_model_id IS NULL
   OR NOT EXISTS (SELECT 1 FROM model_signs p
                  WHERE p.model_id = m.parent_model_id AND p.sign_id = ms.sign_id);

CREATE VIEW v_recognizable_signs AS
SELECT m.sign_language_id, ms.sign_id, m.id AS model_id, ms.class_index
FROM ai_models m
JOIN model_signs ms ON ms.model_id = m.id
WHERE m.is_current;

-- ---------- audit log (append-only) ----------------------------------
CREATE TABLE audit_logs (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor_id    uuid,                             -- deliberately NO foreign key: log must be immutable
  action      text NOT NULL,
  entity_type text NOT NULL,
  entity_id   text,
  before_data jsonb,
  after_data  jsonb,
  ip          inet,
  user_agent  text,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_logs_actor_idx  ON audit_logs (actor_id, created_at DESC);
CREATE INDEX audit_logs_entity_idx ON audit_logs (entity_type, entity_id);

CREATE FUNCTION audit_logs_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_logs is append-only' USING ERRCODE = 'restrict_violation';
END $$;
CREATE TRIGGER audit_logs_no_change BEFORE UPDATE OR DELETE ON audit_logs
  FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();

-- Atomic publish / rollback of the current model (per sign language).
CREATE FUNCTION set_current_model(p_model_id uuid, p_actor uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  v_lang smallint;
  v_old  uuid;
BEGIN
  SELECT sign_language_id INTO v_lang FROM ai_models WHERE id = p_model_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'model % not found', p_model_id;
  END IF;
  SELECT id INTO v_old FROM ai_models
   WHERE sign_language_id = v_lang AND is_current FOR UPDATE;
  UPDATE ai_models SET is_current = false
   WHERE sign_language_id = v_lang AND is_current;
  UPDATE ai_models
     SET is_current = true, published_by = p_actor,
         published_at = COALESCE(published_at, now())
   WHERE id = p_model_id;
  INSERT INTO audit_logs (actor_id, action, entity_type, entity_id, before_data, after_data)
  VALUES (p_actor, 'model.set_current', 'ai_models', p_model_id::text,
          jsonb_build_object('previous', v_old), jsonb_build_object('current', p_model_id));
END $$;

-- ---------- history, feedback, reports, notifications ----------------
CREATE TABLE translation_history (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id          uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  mode             text NOT NULL CHECK (mode IN ('sign_to_text','speech_to_sign')),
  text_result      text NOT NULL,
  language_code    text NOT NULL REFERENCES languages(code),
  sign_language_id smallint REFERENCES sign_languages(id),
  model_id         uuid REFERENCES ai_models(id) ON DELETE SET NULL,
  avg_confidence   real CHECK (avg_confidence BETWEEN 0 AND 1),
  signs            jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(signs) = 'array'),
  is_favorite      boolean NOT NULL DEFAULT false,
  created_at       timestamptz NOT NULL DEFAULT now()
  -- no video, no landmarks: history holds text only
);
CREATE INDEX translation_history_user_idx ON translation_history (user_id, created_at DESC);
CREATE INDEX translation_history_fav_idx  ON translation_history (user_id) WHERE is_favorite;

CREATE TABLE feedback (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id    uuid REFERENCES users(id) ON DELETE SET NULL,
  history_id bigint REFERENCES translation_history(id) ON DELETE SET NULL,
  rating     smallint CHECK (rating BETWEEN 1 AND 5),
  comment    text CHECK (comment IS NULL OR char_length(comment) <= 2000),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reports (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id          uuid REFERENCES users(id) ON DELETE SET NULL,
  kind             text NOT NULL CHECK (kind IN ('recognition_error','bug','abuse','other')),
  history_id       bigint REFERENCES translation_history(id) ON DELETE SET NULL,
  shown_sign_id    uuid REFERENCES sign_dictionary(id) ON DELETE SET NULL,
  expected_sign_id uuid REFERENCES sign_dictionary(id) ON DELETE SET NULL,
  description      text CHECK (description IS NULL OR char_length(description) <= 4000),
  status           text NOT NULL DEFAULT 'open' CHECK (status IN ('open','triaged','resolved','dismissed')),
  assigned_to      uuid REFERENCES users(id) ON DELETE SET NULL,
  resolved_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX reports_status_idx ON reports (status, created_at DESC);
CREATE TRIGGER reports_updated BEFORE UPDATE ON reports
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE notifications (
  id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind       text NOT NULL,
  title      text NOT NULL,
  body       text,
  data       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(data) = 'object'),
  read_at    timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX notifications_unread_idx ON notifications (user_id, created_at DESC) WHERE read_at IS NULL;

-- ---------- API keys & system settings -------------------------------
CREATE TABLE api_keys (
  id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name                  text NOT NULL,
  key_prefix            text NOT NULL UNIQUE, -- first chars, used for lookup only
  key_hash              text NOT NULL,        -- SHA-256 of the full key; key shown once
  scopes                text[] NOT NULL DEFAULT '{}',
  rate_limit_per_minute integer NOT NULL DEFAULT 60 CHECK (rate_limit_per_minute > 0),
  last_used_at          timestamptz,
  expires_at            timestamptz,
  revoked_at            timestamptz,
  created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX api_keys_owner_idx ON api_keys (owner_id);

CREATE TABLE system_settings (
  key         text PRIMARY KEY CHECK (key ~ '^[a-z0-9_.]+$'),
  value       jsonb NOT NULL,
  description text,
  updated_by  uuid REFERENCES users(id) ON DELETE SET NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER system_settings_updated BEFORE UPDATE ON system_settings
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
-- Starting values only; to be tuned by experiment. Never store secrets here.
INSERT INTO system_settings (key, value, description) VALUES
  ('dataset.min_signers_per_sign', '3',  'Starting value: min distinct signers before a new sign may be trained'),
  ('dataset.min_samples_per_sign', '15', 'Starting value: min QC-passed verified samples per new sign');

-- ---------- background jobs (PostgreSQL-backed queue) ----------------
CREATE TABLE jobs (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind         text NOT NULL CHECK (kind IN
                 ('extract_landmarks','qc_sample','train_model','evaluate_model','export_model','delete_files')),
  status       text NOT NULL DEFAULT 'queued'
                 CHECK (status IN ('queued','running','succeeded','failed','canceled')),
  payload      jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
  result       jsonb,
  error        text,
  attempts     integer NOT NULL DEFAULT 0,
  max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
  locked_by    text,
  locked_at    timestamptz,
  run_after    timestamptz NOT NULL DEFAULT now(),
  created_by   uuid REFERENCES users(id) ON DELETE SET NULL,
  created_at   timestamptz NOT NULL DEFAULT now(),
  started_at   timestamptz,
  finished_at  timestamptz
);
CREATE INDEX jobs_queue_idx ON jobs (run_after) WHERE status = 'queued';

-- ---------- privacy procedures ---------------------------------------
-- Account deletion: personal data is erased, accounting rows are kept anonymously.
CREATE FUNCTION anonymize_user(p_user uuid, p_actor uuid) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE users
     SET email = 'deleted-' || id::text || '@deleted.invalid',
         password_hash = NULL, status = 'deleted', blocked_reason = NULL,
         deleted_at = now()
   WHERE id = p_user;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'user % not found', p_user;
  END IF;
  DELETE FROM profiles            WHERE user_id = p_user;
  DELETE FROM translation_history WHERE user_id = p_user;
  DELETE FROM notifications       WHERE user_id = p_user;
  DELETE FROM api_keys            WHERE owner_id = p_user;
  DELETE FROM refresh_tokens      WHERE user_id = p_user;
  DELETE FROM user_roles          WHERE user_id = p_user;
  UPDATE feedback SET user_id = NULL WHERE user_id = p_user;
  UPDATE reports  SET user_id = NULL WHERE user_id = p_user;
  UPDATE signers  SET user_id = NULL WHERE user_id = p_user;
  INSERT INTO audit_logs (actor_id, action, entity_type, entity_id)
  VALUES (p_actor, 'user.anonymize', 'users', p_user::text);
END $$;

-- Consent revocation: samples are soft-deleted and file deletion is queued.
CREATE FUNCTION revoke_consent(p_consent uuid, p_actor uuid) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE consents SET revoked_at = now() WHERE id = p_consent AND revoked_at IS NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'consent % not found or already revoked', p_consent;
  END IF;
  UPDATE dataset_samples SET deleted_at = now()
   WHERE consent_id = p_consent AND deleted_at IS NULL;
  INSERT INTO jobs (kind, payload)
  SELECT 'delete_files',
         jsonb_build_object('sample_id', id, 'video_key', video_key, 'landmarks_key', landmarks_key)
    FROM dataset_samples WHERE consent_id = p_consent;
  INSERT INTO audit_logs (actor_id, action, entity_type, entity_id)
  VALUES (p_actor, 'consent.revoke', 'consents', p_consent::text);
END $$;

COMMIT;

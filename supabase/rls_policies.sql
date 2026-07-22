/*
  CVP-IA — Row Level Security
  Apply in Supabase SQL Editor (or via CLI) BEFORE publishing the panel.

  Goal: anon key in the browser is read-only.
  Writes must use the service_role key only on the ETL machine (.env, never in HTML).
*/

-- Enable RLS on core tables (safe if already enabled)
ALTER TABLE IF EXISTS parlamentares ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS scores ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS despesas ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS votos ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS votacoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS candidaturas_tse ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS proposicoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS fornecedores ENABLE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS pipeline_runs ENABLE ROW LEVEL SECURITY;

-- Drop old permissive policies if re-running
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT schemaname, tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN (
        'parlamentares','scores','despesas','votos','votacoes',
        'candidaturas_tse','proposicoes','fornecedores','pipeline_runs'
      )
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', r.policyname, r.schemaname, r.tablename);
  END LOOP;
END $$;

-- Public read for anon + authenticated
CREATE POLICY parlamentares_select_public ON parlamentares FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY scores_select_public ON scores FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY despesas_select_public ON despesas FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY votos_select_public ON votos FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY votacoes_select_public ON votacoes FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY candidaturas_tse_select_public ON candidaturas_tse FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY proposicoes_select_public ON proposicoes FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY fornecedores_select_public ON fornecedores FOR SELECT TO anon, authenticated USING (true);
CREATE POLICY pipeline_runs_select_public ON pipeline_runs FOR SELECT TO anon, authenticated USING (true);

-- Explicitly no INSERT/UPDATE/DELETE policies for anon/authenticated.
-- service_role bypasses RLS and is used only by the ETL.

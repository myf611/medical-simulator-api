-- Organizations (hospitals/universities)
CREATE TABLE IF NOT EXISTS organizations (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT UNIQUE NOT NULL,
  country TEXT DEFAULT 'UZ',
  plan TEXT DEFAULT 'starter',
  max_students INTEGER DEFAULT 30,
  subscription_end TIMESTAMPTZ,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Students
CREATE TABLE IF NOT EXISTS students (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  organization_id UUID REFERENCES organizations(id),
  last_name TEXT NOT NULL,
  first_name TEXT NOT NULL,
  phone TEXT UNIQUE NOT NULL,
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Cases
CREATE TABLE IF NOT EXISTS cases (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  organization_id UUID REFERENCES organizations(id),
  title TEXT NOT NULL,
  difficulty TEXT DEFAULT 'medium',
  specialty TEXT DEFAULT 'endocrinology',
  is_active BOOLEAN DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Attempts (exam sessions)
CREATE TABLE IF NOT EXISTS attempts (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  student_id UUID REFERENCES students(id),
  case_id UUID NOT NULL,
  organization_id UUID REFERENCES organizations(id),
  started_at TIMESTAMPTZ DEFAULT NOW(),
  finished_at TIMESTAMPTZ,
  grade TEXT,
  diagnosis TEXT,
  workup_plan TEXT,
  treatment_plan TEXT,
  transcript JSONB DEFAULT '[]',
  duration_seconds INTEGER
);

-- Insert default organization for testing
INSERT INTO organizations (name, slug, max_students, subscription_end, is_active)
VALUES ('Центр эндокринологии Ташкент', 'tashkent-endo', 100, '2027-12-31', true)
ON CONFLICT (slug) DO NOTHING;


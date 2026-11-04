-- ==============================================================================
-- ECOSISTEMA DOCENCIA IA UDP — ESQUEMA RELACIONAL INSTITUCIONAL (SUPABASE / POSTGRESQL)
-- Arquitectura Normalizada (3NF/BCNF) de Alto Rendimiento y Escalabilidad Universitaria
-- ==============================================================================

-- 1. EXTENSIONES REQUERIDAS
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "btree_gist";

-- Función global para actualización automática de updated_at
CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ------------------------------------------------------------------------------
-- 2. JERARQUÍA INSTITUCIONAL Y MULTI-TENANT (UDP Y MULTI-UNIVERSIDAD)
-- ------------------------------------------------------------------------------

-- 2.1 Institución (UDP, etc.)
CREATE TABLE IF NOT EXISTS public.institutions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    code VARCHAR(30) UNIQUE NOT NULL,              -- ej. 'UDP'
    name VARCHAR(255) NOT NULL,                    -- 'Universidad Diego Portales'
    domain VARCHAR(100) NOT NULL,                  -- 'mail.udp.cl'
    canvas_base_url VARCHAR(255) NULL,             -- 'https://udp.instructure.com'
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2.2 Facultades
CREATE TABLE IF NOT EXISTS public.faculties (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    institution_id UUID NOT NULL REFERENCES public.institutions(id) ON DELETE CASCADE,
    code VARCHAR(50) NOT NULL,                     -- ej. 'FING'
    name VARCHAR(255) NOT NULL,                    -- 'Facultad de Ingeniería y Ciencias'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_faculty_institution_code UNIQUE (institution_id, code)
);

-- 2.3 Carreras / Departamentos Académicos
CREATE TABLE IF NOT EXISTS public.academic_programs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    faculty_id UUID NOT NULL REFERENCES public.faculties(id) ON DELETE CASCADE,
    code VARCHAR(50) NOT NULL,                     -- ej. 'INF_TELE'
    name VARCHAR(255) NOT NULL,                    -- 'Escuela de Informática y Telecomunicaciones'
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_program_faculty_code UNIQUE (faculty_id, code)
);

-- 2.4 Periodos Académicos (Semestres)
CREATE TABLE IF NOT EXISTS public.academic_terms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    institution_id UUID NOT NULL REFERENCES public.institutions(id) ON DELETE CASCADE,
    code VARCHAR(20) NOT NULL,                     -- ej. '2026-01', '2026-02'
    name VARCHAR(100) NOT NULL,                    -- ej. 'Primer Semestre 2026'
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_term_institution_code UNIQUE (institution_id, code)
);

-- ------------------------------------------------------------------------------
-- 3. CURSOS Y SECCIONES (MODELO ACADÉMICO NORMALIZADO)
-- ------------------------------------------------------------------------------

-- 3.1 Cursos Institucionales
CREATE TABLE IF NOT EXISTS public.courses (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    institution_id UUID NULL REFERENCES public.institutions(id) ON DELETE SET NULL,
    program_id UUID NULL REFERENCES public.academic_programs(id) ON DELETE SET NULL,
    canvas_course_id BIGINT UNIQUE NOT NULL,       -- ID numérico de Canvas UDP (ej. 47552)
    code VARCHAR(50) NOT NULL,                    -- Código oficial asignatura (ej. CIT3203)
    name VARCHAR(255) NOT NULL,                   -- 'PROYECTO EN TICS II'
    term VARCHAR(20) DEFAULT '2026-02',           -- Semestre actual para compatibilidad
    term_id UUID NULL REFERENCES public.academic_terms(id) ON DELETE SET NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3.2 Secciones por Curso (1 Curso Canvas = 1 Sección matriculada)
CREATE TABLE IF NOT EXISTS public.sections (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    course_id UUID NOT NULL REFERENCES public.courses(id) ON DELETE CASCADE,
    canvas_section_id BIGINT NULL,
    code VARCHAR(50) NOT NULL,                    -- ej. CIT3203_CA01
    name VARCHAR(100) NOT NULL,                   -- ej. Sección 1
    teacher_name VARCHAR(255) NULL,
    teacher_email VARCHAR(255) NULL,
    assistant_name VARCHAR(255) NULL,
    assistant_email VARCHAR(255) NULL,
    
    -- Configuración Horario Cátedra (Compatibilidad plana)
    horario_catedra_dias INT[] DEFAULT '{3}',     -- 1=Lun, 2=Mar, 3=Mié, 4=Jue, 5=Vie
    horario_catedra_inicio TIME DEFAULT '14:30',
    horario_catedra_fin TIME DEFAULT '17:30',
    horario_catedra_sala VARCHAR(100) DEFAULT 'SALA X',

    -- Configuración Horario Ayudantía (Compatibilidad plana)
    horario_ayudantia_dias INT[] DEFAULT '{3}',
    horario_ayudantia_inicio TIME DEFAULT '16:00',
    horario_ayudantia_fin TIME DEFAULT '17:20',
    horario_ayudantia_sala VARCHAR(100) DEFAULT 'SALA X',
    -- Segundo horario opcional para marcar la MISMA ayudantía semanal (ej. {"dias":[3],"horaInicio":"20:10","horaFin":"21:30","sala":"SALA X"})
    horario_ayudantia2 JSONB NULL,

    -- Parámetros Operativos y Seguridad
    pin_activo VARCHAR(10) DEFAULT '4821',
    requiere_pin BOOLEAN DEFAULT TRUE,
    requiere_geo BOOLEAN DEFAULT FALSE,
    incluir_ayudantias_en_final BOOLEAN DEFAULT FALSE,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_section_course_code UNIQUE (course_id, code)
);
-- Migración para bases existentes
ALTER TABLE public.sections ADD COLUMN IF NOT EXISTS horario_ayudantia2 JSONB NULL;

-- 3.3 Horarios Detallados Multi-bloque (Soporte para cátedras con múltiples días/bloques)
CREATE TABLE IF NOT EXISTS public.section_schedules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    section_id UUID NOT NULL REFERENCES public.sections(id) ON DELETE CASCADE,
    type VARCHAR(20) NOT NULL CHECK (type IN ('catedra', 'ayudantia', 'laboratorio', 'taller')),
    day_of_week SMALLINT NOT NULL CHECK (day_of_week BETWEEN 1 AND 7), -- 1=Lunes, 7=Domingo
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    room VARCHAR(100) NULL,
    building VARCHAR(100) NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT chk_schedule_time CHECK (start_time < end_time)
);

-- ------------------------------------------------------------------------------
-- 4. ESTUDIANTES Y MATRÍCULA (NÓMINA INSTITUCIONAL CANVAS UDP)
-- ------------------------------------------------------------------------------

-- 4.0 Usuarios de la app (docentes, ayudantes, administradores). Autenticación propia del servidor
--     (password_hash nunca se expone: sin privilegios para anon/authenticated). Antes la creaba
--     scripts/normalize_canvas_schema.js; se declara aquí para que el esquema sea autocontenido.
CREATE TABLE IF NOT EXISTS public.users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canvas_user_id BIGINT UNIQUE NULL,            -- Vincula con students.canvas_id cuando el usuario es estudiante
    rut VARCHAR(20) UNIQUE NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    username VARCHAR(100) UNIQUE NULL,
    name VARCHAR(255) NOT NULL,
    role_global VARCHAR(50) DEFAULT 'admin',
    is_owner BOOLEAN DEFAULT FALSE,
    salt VARCHAR(64) NULL,
    password_hash TEXT NULL,
    avatar_url TEXT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    auth_version INTEGER NOT NULL DEFAULT 1 CHECK (auth_version >= 1),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS auth_version INTEGER NOT NULL DEFAULT 1 CHECK (auth_version >= 1);
CREATE INDEX IF NOT EXISTS idx_users_email ON public.users(LOWER(email));
CREATE INDEX IF NOT EXISTS idx_users_canvas_id ON public.users(canvas_user_id);

-- 4.1 Estudiantes
CREATE TABLE IF NOT EXISTS public.students (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    canvas_id BIGINT UNIQUE NOT NULL,             -- ID único Canvas (ej. 29248)
    rut VARCHAR(20) NOT NULL UNIQUE,              -- RUT institucional validado (ej. 20.481.932-8)
    nombres VARCHAR(150) NOT NULL,
    apellidos VARCHAR(150) NOT NULL,
    email VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4.2 Matrícula / Enrollments (Relación Estudiante <-> Sección)
CREATE TABLE IF NOT EXISTS public.enrollments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    section_id UUID NOT NULL REFERENCES public.sections(id) ON DELETE CASCADE,
    student_id UUID NULL REFERENCES public.students(id) ON DELETE CASCADE,   -- Alumnos
    user_id UUID NULL REFERENCES public.users(id) ON DELETE CASCADE,         -- Docentes / ayudantes / observadores
    enrollment_type VARCHAR(50) DEFAULT 'StudentEnrollment',                 -- TeacherEnrollment | TaEnrollment | ObserverEnrollment | StudentEnrollment
    canvas_enrollment_id BIGINT NULL,
    role VARCHAR(20) DEFAULT 'student' CHECK (role IN ('student', 'assistant', 'teacher', 'observer')),
    status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active', 'withdrawn', 'suspended')),
    enrolled_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_enrollment UNIQUE (section_id, student_id)
);
-- Migración para bases existentes (antes en scripts/normalize_canvas_schema.js)
ALTER TABLE public.enrollments ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES public.users(id) ON DELETE CASCADE;
ALTER TABLE public.enrollments ADD COLUMN IF NOT EXISTS enrollment_type VARCHAR(50) DEFAULT 'StudentEnrollment';
ALTER TABLE public.enrollments ADD COLUMN IF NOT EXISTS canvas_enrollment_id BIGINT NULL;
ALTER TABLE public.enrollments ALTER COLUMN student_id DROP NOT NULL;

-- ------------------------------------------------------------------------------
-- 5. SESIONES EFECTIVAS DE CLASES (ASISTENCIA Y PROGRAMACIÓN)
-- ------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.class_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    section_id UUID NOT NULL REFERENCES public.sections(id) ON DELETE CASCADE,
    session_code VARCHAR(100) NULL,               -- Identificador semántico (ej. sess_CIT3203_CA01_ayu_2)
    date DATE NOT NULL,                           -- Fecha de la clase YYYY-MM-DD
    dia_semana VARCHAR(20) NOT NULL,              -- Miércoles, Jueves, etc.
    type VARCHAR(20) NOT NULL CHECK (type IN ('catedra', 'ayudantia', 'laboratorio')),
    modality VARCHAR(20) DEFAULT 'presencial' CHECK (modality IN ('presencial', 'online')),
    status VARCHAR(20) DEFAULT 'programada' CHECK (status IN ('programada', 'en_curso', 'realizada', 'cancelada')),
    motivo_cancelacion TEXT NULL,
    start_time TIME NULL,
    end_time TIME NULL,
    room VARCHAR(100) NULL,
    pin VARCHAR(10) NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_session_date_type UNIQUE (section_id, date, type)
);

-- ------------------------------------------------------------------------------
-- 6. REGISTROS DE ASISTENCIA (ALTO VOLUMEN: ESCALABLE A MILLONES DE FILAS)
-- ------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.attendance_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES public.class_sessions(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.students(id) ON DELETE CASCADE,
    value SMALLINT NOT NULL DEFAULT 0 CHECK (value IN (0, 1)), -- 1 = Presente, 0 = Ausente
    marked_by VARCHAR(50) DEFAULT 'profesor' CHECK (marked_by IN ('profesor', 'ayudante', 'alumno_pin', 'sistema', 'alumno_link', 'alumno_qr')),
    marked_at TIMESTAMPTZ DEFAULT NOW(),
    ip_address INET NULL,
    latitude NUMERIC(10, 7) NULL,
    longitude NUMERIC(10, 7) NULL,
    CONSTRAINT uq_attendance_session_student UNIQUE (session_id, student_id)
);

-- ------------------------------------------------------------------------------
-- 7. DÉCIMAS, TRABAJOS Y EVALUACIONES DE AYUDANTÍA (ESCALABILIDAD EXCEL + ACTIVIDADES)
-- ------------------------------------------------------------------------------

-- 7.1 Registro de Trabajo y Décimas por Estudiante y Sección (Reemplazo inmediato de papel a Excel)
CREATE TABLE IF NOT EXISTS public.student_work_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    section_id UUID NOT NULL REFERENCES public.sections(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.students(id) ON DELETE CASCADE,
    decimas_acumuladas NUMERIC(4, 2) NOT NULL DEFAULT 0.0 CHECK (decimas_acumuladas >= 0),
    trabajos_realizados INT NOT NULL DEFAULT 0 CHECK (trabajos_realizados >= 0),
    observaciones TEXT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_student_work_section UNIQUE (section_id, student_id)
);

-- 7.2 Actividades de Ayudantía / Talleres (Preparado para cuando se conecte con el generador de actividades IA)
CREATE TABLE IF NOT EXISTS public.activities (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    section_id UUID NOT NULL REFERENCES public.sections(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,                  -- ej. 'Taller 1: Modelado de Clases'
    description TEXT NULL,
    type VARCHAR(30) DEFAULT 'taller_ayudantia' CHECK (type IN ('taller_ayudantia', 'control', 'preparacion_solemne', 'tarea')),
    max_decimas NUMERIC(4, 2) DEFAULT 0.5,        -- Tope de décimas a entregar
    due_date TIMESTAMPTZ NULL,
    rubric_json JSONB NULL,                       -- Criterios y pautas generadas por el Agente IA
    is_published BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7.3 Entregas de Estudiantes por Actividad (Submissions)
CREATE TABLE IF NOT EXISTS public.activity_submissions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    activity_id UUID NOT NULL REFERENCES public.activities(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.students(id) ON DELETE CASCADE,
    status VARCHAR(30) DEFAULT 'entregado' CHECK (status IN ('pendiente', 'entregado', 'corregido', 'tardio')),
    decimas_obtained NUMERIC(4, 2) DEFAULT 0.0,
    feedback_text TEXT NULL,                      -- Retroalimentación del Agente o Ayudante
    evaluated_by VARCHAR(50) DEFAULT 'ayudante',
    evaluated_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_activity_student_sub UNIQUE (activity_id, student_id)
);

-- ------------------------------------------------------------------------------
-- 8. AUDITORÍA INSTITUCIONAL DE CAMBIOS (CUMPLIMIENTO UDP)
-- ------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS public.attendance_audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    session_id UUID NOT NULL REFERENCES public.class_sessions(id) ON DELETE CASCADE,
    student_id UUID NOT NULL REFERENCES public.students(id) ON DELETE CASCADE,
    previous_value SMALLINT NULL,
    new_value SMALLINT NOT NULL,
    reason TEXT NULL,                             -- ej. 'Certificado médico presentado'
    changed_by VARCHAR(100) NOT NULL,             -- Usuario/email del docente o ayudante
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- ------------------------------------------------------------------------------
-- 9. ÍNDICES DE ALTO RENDIMIENTO (OPTIMIZADOS PARA INDEX-ONLY SCANS A ESCALA UNIVERSITARIA)
-- ------------------------------------------------------------------------------

-- Secciones y Matrícula
CREATE INDEX IF NOT EXISTS idx_sections_course ON public.sections(course_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_section ON public.enrollments(section_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_student ON public.enrollments(student_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_user_id ON public.enrollments(user_id);

-- Sesiones
CREATE INDEX IF NOT EXISTS idx_sessions_section_date ON public.class_sessions(section_id, date);
CREATE INDEX IF NOT EXISTS idx_sessions_sec_date_type ON public.class_sessions(section_id, date, type);
CREATE INDEX IF NOT EXISTS idx_sessions_code ON public.class_sessions(session_code);

-- Asistencia: Índices covering con INCLUDE para conteo relámpago sin tocar disco (Heap)
CREATE INDEX IF NOT EXISTS idx_attendance_records_covering 
ON public.attendance_records(session_id, value) 
INCLUDE (student_id);

CREATE INDEX IF NOT EXISTS idx_attendance_student_covering 
ON public.attendance_records(student_id, value) 
INCLUDE (session_id);

-- Décimas y Trabajos
CREATE INDEX IF NOT EXISTS idx_work_records_lookup 
ON public.student_work_records(section_id, student_id);

-- Actividades
CREATE INDEX IF NOT EXISTS idx_activities_section ON public.activities(section_id);
CREATE INDEX IF NOT EXISTS idx_activity_submissions_act ON public.activity_submissions(activity_id);
CREATE INDEX IF NOT EXISTS idx_activity_submissions_stu ON public.activity_submissions(student_id);

-- ------------------------------------------------------------------------------
-- 10. VISTAS RESUMEN DE RENDIMIENTO
-- ------------------------------------------------------------------------------

-- security_invoker: respeta RLS de quien consulta (sin él, la vista correría como su dueño)
CREATE OR REPLACE VIEW public.v_student_attendance_summary WITH (security_invoker = true) AS
SELECT 
    e.section_id,
    s.id AS student_id,
    s.canvas_id,
    s.rut,
    s.nombres,
    s.apellidos,
    s.email,
    -- Décimas y Trabajos registrados en Ayudantía
    COALESCE(wr.decimas_acumuladas, 0) AS decimas_acumuladas,
    COALESCE(wr.trabajos_realizados, 0) AS trabajos_realizados,
    -- Cátedras
    COUNT(CASE WHEN cs.type = 'catedra' AND cs.status != 'cancelada' AND ar.value = 1 THEN 1 END) AS catedras_asistidas,
    COUNT(CASE WHEN cs.type = 'catedra' AND cs.status != 'cancelada' THEN 1 END) AS catedras_validas,
    CASE 
        WHEN COUNT(CASE WHEN cs.type = 'catedra' AND cs.status != 'cancelada' THEN 1 END) > 0 THEN
            ROUND((COUNT(CASE WHEN cs.type = 'catedra' AND cs.status != 'cancelada' AND ar.value = 1 THEN 1 END)::NUMERIC / 
                   COUNT(CASE WHEN cs.type = 'catedra' AND cs.status != 'cancelada' THEN 1 END)::NUMERIC) * 100)
        ELSE 0 
    END AS catedras_pct,
    -- Ayudantías
    COUNT(CASE WHEN cs.type = 'ayudantia' AND cs.status != 'cancelada' AND ar.value = 1 THEN 1 END) AS ayudantias_asistidas,
    COUNT(CASE WHEN cs.type = 'ayudantia' AND cs.status != 'cancelada' THEN 1 END) AS ayudantias_validas,
    CASE 
        WHEN COUNT(CASE WHEN cs.type = 'ayudantia' AND cs.status != 'cancelada' THEN 1 END) > 0 THEN
            ROUND((COUNT(CASE WHEN cs.type = 'ayudantia' AND cs.status != 'cancelada' AND ar.value = 1 THEN 1 END)::NUMERIC / 
                   COUNT(CASE WHEN cs.type = 'ayudantia' AND cs.status != 'cancelada' THEN 1 END)::NUMERIC) * 100)
        ELSE 0 
    END AS ayudantias_pct,
    -- Totales Combinados
    COUNT(CASE WHEN cs.status != 'cancelada' AND ar.value = 1 THEN 1 END) AS total_asistidas,
    COUNT(CASE WHEN cs.status != 'cancelada' THEN 1 END) AS total_validas,
    CASE 
        WHEN COUNT(CASE WHEN cs.status != 'cancelada' THEN 1 END) > 0 THEN
            ROUND((COUNT(CASE WHEN cs.status != 'cancelada' AND ar.value = 1 THEN 1 END)::NUMERIC / 
                   COUNT(CASE WHEN cs.status != 'cancelada' THEN 1 END)::NUMERIC) * 100)
        ELSE 0 
    END AS total_pct
FROM public.enrollments e
JOIN public.students s ON s.id = e.student_id
LEFT JOIN public.student_work_records wr ON wr.section_id = e.section_id AND wr.student_id = s.id
LEFT JOIN public.class_sessions cs ON cs.section_id = e.section_id
LEFT JOIN public.attendance_records ar ON ar.session_id = cs.id AND ar.student_id = s.id
GROUP BY e.section_id, s.id, s.canvas_id, s.rut, s.nombres, s.apellidos, s.email, wr.decimas_acumuladas, wr.trabajos_realizados;

-- ------------------------------------------------------------------------------
-- 11. PROCEDIMIENTOS ALMACENADOS / RPCs DE ALTA EFICIENCIA
-- ------------------------------------------------------------------------------

-- 11.1 Marcación individual de asistencia por código semántico
CREATE OR REPLACE FUNCTION public.upsert_attendance_by_code(
    p_session_code TEXT,
    p_student_canvas_id BIGINT,
    p_value SMALLINT,
    p_marked_by TEXT DEFAULT 'profesor'
)
RETURNS VOID AS $$
DECLARE
    v_session_id UUID;
    v_student_id UUID;
BEGIN
    SELECT id INTO v_session_id FROM public.class_sessions WHERE session_code = p_session_code LIMIT 1;
    SELECT id INTO v_student_id FROM public.students WHERE canvas_id = p_student_canvas_id LIMIT 1;

    IF v_session_id IS NOT NULL AND v_student_id IS NOT NULL THEN
        INSERT INTO public.attendance_records (session_id, student_id, value, marked_by, marked_at)
        VALUES (v_session_id, v_student_id, p_value, COALESCE(p_marked_by, 'profesor'), NOW())
        ON CONFLICT (session_id, student_id)
        DO UPDATE SET 
            value = CASE
                WHEN attendance_records.value = 1 
                     AND attendance_records.marked_by IN ('alumno_link', 'alumno_pin', 'alumno_qr') 
                     AND EXCLUDED.value = 0 
                     AND EXCLUDED.marked_by NOT IN ('profesor', 'ayudante') THEN 1
                ELSE EXCLUDED.value
            END,
            marked_by = CASE
                WHEN attendance_records.value = 1 
                     AND attendance_records.marked_by IN ('alumno_link', 'alumno_pin', 'alumno_qr') 
                     AND EXCLUDED.value = 0 
                     AND EXCLUDED.marked_by NOT IN ('profesor', 'ayudante') THEN attendance_records.marked_by
                ELSE EXCLUDED.marked_by
            END,
            marked_at = NOW();
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 11.2 Inserción masiva atómica de asistencias por códigos semánticos
CREATE OR REPLACE FUNCTION public.upsert_attendance_batch_by_codes(
    records_json JSONB
)
RETURNS VOID AS $$
BEGIN
    INSERT INTO public.attendance_records (session_id, student_id, value, marked_by, marked_at)
    SELECT 
        cs.id AS session_id,
        st.id AS student_id,
        (r->>'value')::SMALLINT AS value,
        COALESCE(r->>'marked_by', 'profesor') AS marked_by,
        NOW() AS marked_at
    FROM jsonb_array_elements(records_json) AS r
    JOIN public.class_sessions cs ON cs.session_code = r->>'session_code'
    JOIN public.students st ON st.canvas_id = (r->>'student_canvas_id')::BIGINT
    ON CONFLICT (session_id, student_id)
    DO UPDATE SET 
        value = CASE
            WHEN attendance_records.value = 1 
                 AND attendance_records.marked_by IN ('alumno_link', 'alumno_pin', 'alumno_qr') 
                 AND EXCLUDED.value = 0 
                 AND EXCLUDED.marked_by NOT IN ('profesor', 'ayudante') THEN 1
            ELSE EXCLUDED.value
        END,
        marked_by = CASE
            WHEN attendance_records.value = 1 
                 AND attendance_records.marked_by IN ('alumno_link', 'alumno_pin', 'alumno_qr') 
                 AND EXCLUDED.value = 0 
                 AND EXCLUDED.marked_by NOT IN ('profesor', 'ayudante') THEN attendance_records.marked_by
            ELSE EXCLUDED.marked_by
        END,
        marked_at = NOW();
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 11.3 Obtención ultra rápida del mapa de asistencia para una sección
CREATE OR REPLACE FUNCTION public.get_section_attendance_map(p_section_code TEXT)
RETURNS TABLE (
    session_code VARCHAR,
    student_canvas_id BIGINT,
    value SMALLINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        cs.session_code,
        st.canvas_id AS student_canvas_id,
        ar.value
    FROM public.attendance_records ar
    JOIN public.class_sessions cs ON cs.id = ar.session_id
    JOIN public.students st ON st.id = ar.student_id
    JOIN public.sections sec ON sec.id = cs.section_id
    WHERE sec.code = p_section_code;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 11.4 Upsert de décimas y trabajos realizados por estudiante y sección
CREATE OR REPLACE FUNCTION public.upsert_student_work_record(
    p_section_code TEXT,
    p_student_canvas_id BIGINT,
    p_decimas NUMERIC,
    p_trabajos INT
)
RETURNS VOID AS $$
DECLARE
    v_section_id UUID;
    v_student_id UUID;
BEGIN
    SELECT id INTO v_section_id FROM public.sections WHERE code = p_section_code LIMIT 1;
    SELECT id INTO v_student_id FROM public.students WHERE canvas_id = p_student_canvas_id LIMIT 1;

    IF v_section_id IS NOT NULL AND v_student_id IS NOT NULL THEN
        INSERT INTO public.student_work_records (section_id, student_id, decimas_acumuladas, trabajos_realizados, updated_at)
        VALUES (v_section_id, v_student_id, p_decimas, p_trabajos, NOW())
        ON CONFLICT (section_id, student_id)
        DO UPDATE SET 
            decimas_acumuladas = EXCLUDED.decimas_acumuladas,
            trabajos_realizados = EXCLUDED.trabajos_realizados,
            updated_at = NOW();
    END IF;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 11.5 Obtener registros de décimas y trabajos de toda la sección
CREATE OR REPLACE FUNCTION public.get_section_work_records(p_section_code TEXT)
RETURNS TABLE (
    student_canvas_id BIGINT,
    decimas_acumuladas NUMERIC,
    trabajos_realizados INT
) AS $$
BEGIN
    RETURN QUERY
    SELECT 
        st.canvas_id AS student_canvas_id,
        wr.decimas_acumuladas,
        wr.trabajos_realizados
    FROM public.student_work_records wr
    JOIN public.students st ON st.id = wr.student_id
    JOIN public.sections sec ON sec.id = wr.section_id
    WHERE sec.code = p_section_code;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ------------------------------------------------------------------------------
-- 12. TRIGGERS PARA UPDATED_AT
-- ------------------------------------------------------------------------------

CREATE OR REPLACE TRIGGER trg_courses_updated_at BEFORE UPDATE ON public.courses FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();
CREATE OR REPLACE TRIGGER trg_sections_updated_at BEFORE UPDATE ON public.sections FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();
CREATE OR REPLACE TRIGGER trg_students_updated_at BEFORE UPDATE ON public.students FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();
CREATE OR REPLACE TRIGGER trg_sessions_updated_at BEFORE UPDATE ON public.class_sessions FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();
CREATE OR REPLACE TRIGGER trg_work_records_updated_at BEFORE UPDATE ON public.student_work_records FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

-- ------------------------------------------------------------------------------
-- 13. SEGURIDAD: RLS POR ROL (estado final de 20261016_fase3c_bloqueo_rls + 20261104_rls_por_rol)
-- ------------------------------------------------------------------------------
-- La app accede con service_role desde el servidor (ignora RLS). anon no tiene acceso a nada.
-- authenticated (JWT de Supabase con "sub" = public.users.id) tiene acceso mínimo por rol en la sección.
-- Mantener sincronizado con supabase/migrations/20261104_rls_por_rol.sql (lo verifica supabase/tests/run.sh).

-- 13.1 Bloqueo base: RLS en todas las tablas y sin privilegios por defecto para anon/authenticated
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.tablename);
    END LOOP;
END $$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES    FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;

-- 13.2 Helpers de identidad y rol (SECURITY DEFINER: leen users/enrollments sin depender de RLS) ---

-- Usuario de la app asociado al JWT (null si no existe o está inactivo)
CREATE OR REPLACE FUNCTION public.current_app_user_id()
RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT u.id FROM public.users u WHERE u.id = auth.uid() AND u.is_active IS TRUE
$$;

-- Fila de students del usuario (vínculo por ID de Canvas)
CREATE OR REPLACE FUNCTION public.current_student_id()
RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT s.id
    FROM public.students s
    JOIN public.users u ON u.canvas_user_id = s.canvas_id
    WHERE u.id = public.current_app_user_id() AND s.is_active IS TRUE
    LIMIT 1
$$;

-- Rol más alto del usuario en la sección: teacher > assistant > observer > student (o null).
-- Docentes y ayudantes se matriculan con user_id + enrollment_type (role queda en 'student' por
-- defecto), por eso enrollment_type manda cuando es de staff.
CREATE OR REPLACE FUNCTION public.current_role_in_section(p_section_id UUID)
RETURNS TEXT
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT r.role_name
    FROM (
        SELECT CASE e.enrollment_type
                   WHEN 'TeacherEnrollment'  THEN 'teacher'
                   WHEN 'TaEnrollment'       THEN 'assistant'
                   WHEN 'ObserverEnrollment' THEN 'observer'
                   ELSE e.role
               END AS role_name
        FROM public.enrollments e
        WHERE e.section_id = p_section_id
          AND COALESCE(e.status, 'active') = 'active'
          AND (
              (e.user_id IS NOT NULL AND e.user_id = public.current_app_user_id())
              OR (e.student_id IS NOT NULL AND e.student_id = public.current_student_id())
          )
    ) r
    ORDER BY CASE r.role_name WHEN 'teacher' THEN 1 WHEN 'assistant' THEN 2 WHEN 'observer' THEN 3 ELSE 4 END
    LIMIT 1
$$;

CREATE OR REPLACE FUNCTION public.is_section_teacher(p_section_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT COALESCE(public.current_role_in_section(p_section_id) = 'teacher', false) $$;

-- Docente o ayudante (ven datos de estudiantes de la sección)
CREATE OR REPLACE FUNCTION public.is_section_staff(p_section_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT COALESCE(public.current_role_in_section(p_section_id) IN ('teacher', 'assistant'), false) $$;

CREATE OR REPLACE FUNCTION public.is_section_member(p_section_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT public.current_role_in_section(p_section_id) IS NOT NULL $$;

-- ¿El estudiante está matriculado en alguna sección donde el usuario es staff?
CREATE OR REPLACE FUNCTION public.is_staff_of_student(p_student_id UUID)
RETURNS BOOLEAN
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.enrollments e
        WHERE e.student_id = p_student_id AND public.is_section_staff(e.section_id)
    )
$$;

CREATE OR REPLACE FUNCTION public.section_of_session(p_session_id UUID)
RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT cs.section_id FROM public.class_sessions cs WHERE cs.id = p_session_id $$;

CREATE OR REPLACE FUNCTION public.section_of_activity(p_activity_id UUID)
RETURNS UUID
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp
AS $$ SELECT a.section_id FROM public.activities a WHERE a.id = p_activity_id $$;

DO $$
DECLARE fn TEXT;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.current_app_user_id()', 'public.current_student_id()',
        'public.current_role_in_section(uuid)', 'public.is_section_teacher(uuid)',
        'public.is_section_staff(uuid)', 'public.is_section_member(uuid)',
        'public.is_staff_of_student(uuid)', 'public.section_of_session(uuid)',
        'public.section_of_activity(uuid)'
    ] LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC, anon', fn);
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO authenticated, service_role', fn);
    END LOOP;
END $$;

-- 13.3 Privilegios: se parte de cero para anon/authenticated y se concede lo mínimo ------------
REVOKE ALL ON ALL TABLES    IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;

-- Los DEFAULT de id llaman a uuid_generate_v4(): quien inserta necesita poder ejecutarla
DO $$
BEGIN
    IF to_regprocedure('uuid_generate_v4()') IS NOT NULL THEN
        EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO authenticated', to_regprocedure('uuid_generate_v4()'));
    END IF;
END $$;

GRANT SELECT ON public.courses TO authenticated;

-- sections: sin pin_activo (el PIN del día solo lo entrega el servidor)
GRANT SELECT (
    id, course_id, canvas_section_id, code, name, teacher_name, teacher_email, assistant_name, assistant_email,
    horario_catedra_dias, horario_catedra_inicio, horario_catedra_fin, horario_catedra_sala,
    horario_ayudantia_dias, horario_ayudantia_inicio, horario_ayudantia_fin, horario_ayudantia_sala,
    horario_ayudantia2, requiere_pin, requiere_geo, incluir_ayudantias_en_final, created_at, updated_at
) ON public.sections TO authenticated;
GRANT UPDATE (
    horario_catedra_dias, horario_catedra_inicio, horario_catedra_fin, horario_catedra_sala,
    horario_ayudantia_dias, horario_ayudantia_inicio, horario_ayudantia_fin, horario_ayudantia_sala,
    horario_ayudantia2, pin_activo, requiere_pin, requiere_geo, incluir_ayudantias_en_final
) ON public.sections TO authenticated;

GRANT SELECT ON public.students TO authenticated;
GRANT SELECT ON public.enrollments TO authenticated;

-- class_sessions: sin pin
GRANT SELECT (
    id, section_id, session_code, date, dia_semana, type, modality, status, motivo_cancelacion,
    start_time, end_time, room, created_at, updated_at
) ON public.class_sessions TO authenticated;
GRANT UPDATE (status, motivo_cancelacion, modality, start_time, end_time, room) ON public.class_sessions TO authenticated;

-- attendance_records: sin IP ni geolocalización; escritura solo vía RPCs del servidor
GRANT SELECT (id, session_id, student_id, value, marked_by, marked_at) ON public.attendance_records TO authenticated;

GRANT SELECT, INSERT, UPDATE ON public.student_work_records TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.activities TO authenticated;
GRANT SELECT, INSERT ON public.activity_submissions TO authenticated;
GRANT UPDATE (status, decimas_obtained, feedback_text, evaluated_by, evaluated_at) ON public.activity_submissions TO authenticated;

-- La vista corre con los permisos de quien consulta (no salta RLS) y sigue cerrada
ALTER VIEW IF EXISTS public.v_student_attendance_summary SET (security_invoker = true);
REVOKE ALL ON public.v_student_attendance_summary FROM anon, authenticated;

-- 13.4 Políticas (reemplazan cualquier política abierta previa) ------------------------------
ALTER TABLE public.courses              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sections             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.students             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.enrollments          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.class_sessions       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attendance_records   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.student_work_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activities           ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.activity_submissions ENABLE ROW LEVEL SECURITY;

-- Políticas abiertas históricas (schema.sql y la migración de asistencia del 2026-10-11)
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['courses', 'sections', 'students', 'enrollments', 'class_sessions',
                             'attendance_records', 'student_work_records', 'activities', 'activity_submissions'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS "lectura" ON public.%I', t);
        EXECUTE format('DROP POLICY IF EXISTS "creacion" ON public.%I', t);
        EXECUTE format('DROP POLICY IF EXISTS "actualizacion" ON public.%I', t);
    END LOOP;
END $$;
DROP POLICY IF EXISTS "Permitir lectura general de cursos" ON public.courses;
DROP POLICY IF EXISTS "Permitir edición general de cursos" ON public.courses;
DROP POLICY IF EXISTS "Permitir lectura de secciones" ON public.sections;
DROP POLICY IF EXISTS "Permitir edición de secciones" ON public.sections;
DROP POLICY IF EXISTS "Permitir lectura de estudiantes" ON public.students;
DROP POLICY IF EXISTS "Permitir edición de estudiantes" ON public.students;
DROP POLICY IF EXISTS "Permitir lectura de matricula" ON public.enrollments;
DROP POLICY IF EXISTS "Permitir edición de matricula" ON public.enrollments;
DROP POLICY IF EXISTS "Permitir lectura de sesiones" ON public.class_sessions;
DROP POLICY IF EXISTS "Permitir edición de sesiones" ON public.class_sessions;
DROP POLICY IF EXISTS "Permitir lectura de asistencia" ON public.attendance_records;
DROP POLICY IF EXISTS "Permitir edición de asistencia" ON public.attendance_records;
DROP POLICY IF EXISTS "Permitir lectura de work records" ON public.student_work_records;
DROP POLICY IF EXISTS "Permitir edición de work records" ON public.student_work_records;

-- courses: miembros de alguna sección del curso
DROP POLICY IF EXISTS rls_courses_select ON public.courses;
CREATE POLICY rls_courses_select ON public.courses FOR SELECT TO authenticated
    USING (EXISTS (SELECT 1 FROM public.sections s WHERE s.course_id = courses.id AND public.is_section_member(s.id)));

-- sections: miembros leen; solo el docente actualiza la configuración
DROP POLICY IF EXISTS rls_sections_select ON public.sections;
CREATE POLICY rls_sections_select ON public.sections FOR SELECT TO authenticated
    USING (public.is_section_member(id));
DROP POLICY IF EXISTS rls_sections_update_teacher ON public.sections;
CREATE POLICY rls_sections_update_teacher ON public.sections FOR UPDATE TO authenticated
    USING (public.is_section_teacher(id)) WITH CHECK (public.is_section_teacher(id));

-- students: cada estudiante solo su fila; docente/ayudante, los de sus secciones (RUT y correo ajenos protegidos)
DROP POLICY IF EXISTS rls_students_select ON public.students;
CREATE POLICY rls_students_select ON public.students FOR SELECT TO authenticated
    USING (id = public.current_student_id() OR public.is_staff_of_student(id));

-- enrollments: staff ve la matrícula de su sección; cada uno, la propia
DROP POLICY IF EXISTS rls_enrollments_select ON public.enrollments;
CREATE POLICY rls_enrollments_select ON public.enrollments FOR SELECT TO authenticated
    USING (
        public.is_section_staff(section_id)
        OR (student_id IS NOT NULL AND student_id = public.current_student_id())
        OR (user_id IS NOT NULL AND user_id = public.current_app_user_id())
    );

-- class_sessions: miembros leen; el docente cambia estado/horario (crear sesiones: solo servidor)
DROP POLICY IF EXISTS rls_class_sessions_select ON public.class_sessions;
CREATE POLICY rls_class_sessions_select ON public.class_sessions FOR SELECT TO authenticated
    USING (public.is_section_member(section_id));
DROP POLICY IF EXISTS rls_class_sessions_update_teacher ON public.class_sessions;
CREATE POLICY rls_class_sessions_update_teacher ON public.class_sessions FOR UPDATE TO authenticated
    USING (public.is_section_teacher(section_id)) WITH CHECK (public.is_section_teacher(section_id));

-- attendance_records: staff lee su sección; estudiante, solo lo suyo. Sin escritura directa.
DROP POLICY IF EXISTS rls_attendance_records_select ON public.attendance_records;
CREATE POLICY rls_attendance_records_select ON public.attendance_records FOR SELECT TO authenticated
    USING (public.is_section_staff(public.section_of_session(session_id)) OR student_id = public.current_student_id());

-- student_work_records: staff lee; docente escribe; estudiante lee lo suyo
DROP POLICY IF EXISTS rls_work_records_select ON public.student_work_records;
CREATE POLICY rls_work_records_select ON public.student_work_records FOR SELECT TO authenticated
    USING (public.is_section_staff(section_id) OR student_id = public.current_student_id());
DROP POLICY IF EXISTS rls_work_records_insert_teacher ON public.student_work_records;
CREATE POLICY rls_work_records_insert_teacher ON public.student_work_records FOR INSERT TO authenticated
    WITH CHECK (public.is_section_teacher(section_id));
DROP POLICY IF EXISTS rls_work_records_update_teacher ON public.student_work_records;
CREATE POLICY rls_work_records_update_teacher ON public.student_work_records FOR UPDATE TO authenticated
    USING (public.is_section_teacher(section_id)) WITH CHECK (public.is_section_teacher(section_id));

-- activities: staff lee; docente gestiona; estudiante ve las publicadas de su sección
DROP POLICY IF EXISTS rls_activities_select ON public.activities;
CREATE POLICY rls_activities_select ON public.activities FOR SELECT TO authenticated
    USING (public.is_section_staff(section_id) OR (is_published AND public.is_section_member(section_id)));
DROP POLICY IF EXISTS rls_activities_insert_teacher ON public.activities;
CREATE POLICY rls_activities_insert_teacher ON public.activities FOR INSERT TO authenticated
    WITH CHECK (public.is_section_teacher(section_id));
DROP POLICY IF EXISTS rls_activities_update_teacher ON public.activities;
CREATE POLICY rls_activities_update_teacher ON public.activities FOR UPDATE TO authenticated
    USING (public.is_section_teacher(section_id)) WITH CHECK (public.is_section_teacher(section_id));
DROP POLICY IF EXISTS rls_activities_delete_teacher ON public.activities;
CREATE POLICY rls_activities_delete_teacher ON public.activities FOR DELETE TO authenticated
    USING (public.is_section_teacher(section_id));

-- activity_submissions: staff lee; docente crea; docente y ayudante corrigen; estudiante lee lo suyo
DROP POLICY IF EXISTS rls_submissions_select ON public.activity_submissions;
CREATE POLICY rls_submissions_select ON public.activity_submissions FOR SELECT TO authenticated
    USING (public.is_section_staff(public.section_of_activity(activity_id)) OR student_id = public.current_student_id());
DROP POLICY IF EXISTS rls_submissions_insert_teacher ON public.activity_submissions;
CREATE POLICY rls_submissions_insert_teacher ON public.activity_submissions FOR INSERT TO authenticated
    WITH CHECK (public.is_section_teacher(public.section_of_activity(activity_id)));
DROP POLICY IF EXISTS rls_submissions_update_staff ON public.activity_submissions;
CREATE POLICY rls_submissions_update_staff ON public.activity_submissions FOR UPDATE TO authenticated
    USING (public.is_section_staff(public.section_of_activity(activity_id)))
    WITH CHECK (public.is_section_staff(public.section_of_activity(activity_id)));

-- Semilla Institucional UDP Inicial (Idempotente)
INSERT INTO public.institutions (code, name, domain, canvas_base_url)
VALUES ('UDP', 'Universidad Diego Portales', 'mail.udp.cl', 'https://udp.instructure.com')
ON CONFLICT (code) DO NOTHING;

-- 14. MODELO DE DATOS IA (sincronizado con 20261105_modelo_ia.sql)
-- [IA-04] Modelo aditivo de IA. Depende de 20261104_rls_por_rol.sql.
-- 2026-11-05 reserva el siguiente prefijo para respetar el orden lexicográfico del harness.
-- Embeddings aprobados: OpenRouter / openai/text-embedding-3-small, 1536 dimensiones.
-- Cambiar de modelo exige migrar la columna embedding, regenerar los embeddings y reindexar;
-- cambiar solamente EMBEDDING_MODEL/EMBEDDING_DIM no convierte los vectores existentes.
BEGIN;

CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;
GRANT USAGE ON SCHEMA extensions TO authenticated, service_role;

CREATE TABLE IF NOT EXISTS public.submission_versions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    submission_id UUID NOT NULL REFERENCES public.activity_submissions(id) ON DELETE CASCADE,
    version_n INTEGER NOT NULL CHECK (version_n > 0),
    storage_path TEXT NOT NULL CHECK (btrim(storage_path) <> ''),
    texto_extraido TEXT NOT NULL DEFAULT '',
    subido_por UUID NOT NULL REFERENCES public.users(id),
    subido_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    atrasada BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (submission_id, version_n)
);

CREATE TABLE IF NOT EXISTS public.rag_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    owner_type TEXT NOT NULL CHECK (owner_type IN ('version', 'rubric', 'course_material')),
    -- version -> submission_versions.id; rubric -> activities.id; course_material -> courses.id.
    -- No existe aún un catálogo persistido de materiales individuales.
    owner_id UUID NOT NULL,
    -- FKs derivadas de la identidad canónica: evitan huérfanos incluso con borrados concurrentes.
    version_owner_id UUID GENERATED ALWAYS AS (CASE WHEN owner_type = 'version' THEN owner_id END) STORED
        REFERENCES public.submission_versions(id) ON DELETE CASCADE,
    rubric_owner_id UUID GENERATED ALWAYS AS (CASE WHEN owner_type = 'rubric' THEN owner_id END) STORED
        REFERENCES public.activities(id) ON DELETE CASCADE,
    course_owner_id UUID GENERATED ALWAYS AS (CASE WHEN owner_type = 'course_material' THEN owner_id END) STORED
        REFERENCES public.courses(id) ON DELETE CASCADE,
    idx INTEGER NOT NULL CHECK (idx >= 0),
    texto TEXT NOT NULL CHECK (btrim(texto) <> ''),
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL,
    embedding extensions.vector(1536),
    CHECK (char_end > char_start AND char_end - char_start = char_length(texto)),
    UNIQUE (owner_type, owner_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding ON public.rag_chunks
    USING hnsw (embedding extensions.vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_rag_chunks_version_owner ON public.rag_chunks(version_owner_id) WHERE version_owner_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rag_chunks_rubric_owner ON public.rag_chunks(rubric_owner_id) WHERE rubric_owner_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rag_chunks_course_owner ON public.rag_chunks(course_owner_id) WHERE course_owner_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.ai_reviews (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    version_id UUID NOT NULL REFERENCES public.submission_versions(id) ON DELETE CASCADE,
    agente TEXT NOT NULL CHECK (btrim(agente) <> ''),
    modelo TEXT NOT NULL CHECK (btrim(modelo) <> ''),
    estado TEXT NOT NULL DEFAULT 'propuesta' CHECK (estado IN ('propuesta', 'confirmada', 'corregida')),
    nota_preliminar NUMERIC(3,2) CHECK (nota_preliminar BETWEEN 1 AND 7),
    decimas_preliminares NUMERIC(4,2) CHECK (decimas_preliminares >= 0),
    resumen TEXT NOT NULL DEFAULT '',
    confirmada_por UUID REFERENCES public.users(id),
    confirmada_en TIMESTAMPTZ,
    nota_final NUMERIC(3,2) CHECK (nota_final BETWEEN 1 AND 7),
    CONSTRAINT ai_reviews_confirmation CHECK (
        (estado = 'confirmada' AND nota_final IS NOT NULL
            AND confirmada_por IS NOT NULL AND confirmada_en IS NOT NULL)
        OR (estado <> 'confirmada' AND nota_final IS NULL
            AND confirmada_por IS NULL AND confirmada_en IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS idx_ai_reviews_version ON public.ai_reviews(version_id);

CREATE TABLE IF NOT EXISTS public.ai_observations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    review_id UUID NOT NULL REFERENCES public.ai_reviews(id) ON DELETE CASCADE,
    criterio TEXT,
    tipo TEXT NOT NULL CHECK (tipo IN ('criterio', 'feedback_no_abordado', 'retroceso', 'inconsistencia', 'atraso')),
    severidad TEXT NOT NULL CHECK (severidad IN ('baja', 'media', 'alta')),
    texto TEXT NOT NULL CHECK (btrim(texto) <> ''),
    cita_texto TEXT NOT NULL CHECK (btrim(cita_texto) <> ''),
    cita_version_id UUID NOT NULL REFERENCES public.submission_versions(id) DEFERRABLE INITIALLY DEFERRED,
    -- Offsets de caracteres Unicode: base cero, intervalo [char_start, char_end).
    char_start INTEGER NOT NULL CHECK (char_start >= 0),
    char_end INTEGER NOT NULL,
    estado_docente TEXT NOT NULL DEFAULT 'pendiente'
        CHECK (estado_docente IN ('pendiente', 'aceptada', 'rechazada', 'editada')),
    CHECK (char_end > char_start AND char_end - char_start = char_length(cita_texto))
);
CREATE INDEX IF NOT EXISTS idx_ai_observations_review ON public.ai_observations(review_id);
CREATE INDEX IF NOT EXISTS idx_ai_observations_cita ON public.ai_observations(cita_version_id);

CREATE TABLE IF NOT EXISTS public.ai_audit_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor UUID REFERENCES public.users(id) ON DELETE SET NULL,
    section_id UUID REFERENCES public.sections(id) ON DELETE SET NULL,
    version_id UUID REFERENCES public.submission_versions(id) ON DELETE SET NULL,
    agente TEXT NOT NULL,
    modelo TEXT NOT NULL,
    tokens_in INTEGER NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
    tokens_out INTEGER NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
    costo_usd NUMERIC(16,10) CHECK (costo_usd >= 0),
    latencia_ms INTEGER NOT NULL DEFAULT 0 CHECK (latencia_ms >= 0),
    ok BOOLEAN NOT NULL,
    -- Solo código sanitizado; nunca prompts, respuestas ni errores crudos del proveedor.
    error TEXT CHECK (error ~ '^[A-Z][A-Z0-9_]{0,79}$'),
    CHECK (NOT ok OR error IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_ai_audit_section_ts ON public.ai_audit_log(section_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ai_audit_version ON public.ai_audit_log(version_id);

-- Reglas de integridad de datos, no helpers nuevos de autorización.
CREATE OR REPLACE FUNCTION public.validate_ai_review_confirmation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE section_id UUID;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF NEW.version_id IS DISTINCT FROM OLD.version_id THEN
            RAISE EXCEPTION 'La versión de una revisión es inmutable' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.estado = 'confirmada' OR NEW.nota_final IS NOT NULL
        OR NEW.confirmada_por IS NOT NULL OR NEW.confirmada_en IS NOT NULL
        OR (TG_OP = 'UPDATE' AND OLD.estado = 'confirmada') THEN
        SELECT public.section_of_activity(s.activity_id) INTO section_id
        FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE v.id = NEW.version_id;
        IF NOT public.is_section_teacher(section_id) THEN
            RAISE EXCEPTION 'Solo el docente de la sección puede confirmar' USING ERRCODE = '42501';
        END IF;
        IF NEW.estado = 'confirmada' THEN
            IF NEW.confirmada_por IS DISTINCT FROM public.current_app_user_id() THEN
                RAISE EXCEPTION 'La confirmación debe identificar al docente actual' USING ERRCODE = '42501';
            END IF;
            -- La fecha la fija el servidor, no un valor arbitrario del cliente.
            NEW.confirmada_en := now();
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_ai_review_confirmation BEFORE INSERT OR UPDATE ON public.ai_reviews
    FOR EACH ROW EXECUTE FUNCTION public.validate_ai_review_confirmation();

CREATE OR REPLACE FUNCTION public.validate_ai_observation_citation()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE source_text TEXT; source_submission UUID; reviewed_submission UUID; reviewed_n INTEGER; cited_n INTEGER;
BEGIN
    SELECT v.texto_extraido, v.submission_id, v.version_n INTO source_text, source_submission, cited_n
    FROM public.submission_versions v WHERE v.id = NEW.cita_version_id;
    SELECT v.submission_id, v.version_n INTO reviewed_submission, reviewed_n
    FROM public.ai_reviews r JOIN public.submission_versions v ON v.id = r.version_id
    WHERE r.id = NEW.review_id;
    IF source_submission IS NULL OR reviewed_submission IS NULL
        OR source_submission IS DISTINCT FROM reviewed_submission OR cited_n > reviewed_n
        OR NEW.cita_texto IS DISTINCT FROM substring(source_text FROM NEW.char_start + 1 FOR NEW.char_end - NEW.char_start) THEN
        RAISE EXCEPTION 'La cita debe ser literal y pertenecer a esta entrega o una versión anterior'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_ai_observation_citation BEFORE INSERT OR UPDATE ON public.ai_observations
    FOR EACH ROW EXECUTE FUNCTION public.validate_ai_observation_citation();

CREATE OR REPLACE FUNCTION public.validate_rag_chunk_owner()
RETURNS TRIGGER LANGUAGE plpgsql SET search_path = public, pg_temp AS $$
DECLARE valid_owner BOOLEAN;
BEGIN
    CASE NEW.owner_type
        WHEN 'version' THEN
            SELECT EXISTS (SELECT 1 FROM public.submission_versions v WHERE v.id = NEW.owner_id
                AND NEW.texto = substring(v.texto_extraido FROM NEW.char_start + 1 FOR NEW.char_end - NEW.char_start)) INTO valid_owner;
        WHEN 'rubric' THEN
            SELECT EXISTS (SELECT 1 FROM public.activities a WHERE a.id = NEW.owner_id) INTO valid_owner;
        WHEN 'course_material' THEN
            SELECT EXISTS (SELECT 1 FROM public.courses c WHERE c.id = NEW.owner_id) INTO valid_owner;
        ELSE valid_owner := false;
    END CASE;
    IF NOT valid_owner THEN
        RAISE EXCEPTION 'Propietario o fragmento RAG inválido' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE OR REPLACE TRIGGER trg_rag_chunk_owner BEFORE INSERT OR UPDATE ON public.rag_chunks
    FOR EACH ROW EXECUTE FUNCTION public.validate_rag_chunk_owner();

CREATE OR REPLACE FUNCTION public.next_version_n(submission_id UUID)
RETURNS INTEGER LANGUAGE plpgsql SECURITY INVOKER SET search_path = public, pg_temp AS $$
DECLARE next_n INTEGER;
BEGIN
    -- El llamador debe insertar la versión en ESTA transacción, antes de COMMIT.
    PERFORM 1 FROM public.activity_submissions s WHERE s.id = $1 FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Entrega inexistente o no autorizada' USING ERRCODE = '23503';
    END IF;
    SELECT COALESCE(max(v.version_n), 0) + 1 INTO next_n
    FROM public.submission_versions v WHERE v.submission_id = $1;
    RETURN next_n;
END $$;

CREATE OR REPLACE FUNCTION public.match_rag_chunks(query_embedding extensions.vector, owner_ids UUID[], k INTEGER)
RETURNS TABLE (id UUID, owner_type TEXT, owner_id UUID, idx INTEGER, texto TEXT,
    char_start INTEGER, char_end INTEGER, similarity DOUBLE PRECISION)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = public, extensions, pg_temp AS $$
BEGIN
    IF query_embedding IS NULL OR extensions.vector_dims(query_embedding) <> 1536 OR k IS NULL OR k < 1 OR k > 100 THEN
        RAISE EXCEPTION 'Vector de 1536 dimensiones y k entre 1 y 100 requeridos' USING ERRCODE = '22023';
    END IF;
    RETURN QUERY SELECT c.id, c.owner_type, c.owner_id, c.idx, c.texto, c.char_start, c.char_end,
        1 - (c.embedding OPERATOR(extensions.<=>) query_embedding)
    FROM public.rag_chunks c
    WHERE c.owner_id = ANY(COALESCE(owner_ids, '{}'::uuid[])) AND c.embedding IS NOT NULL
    ORDER BY c.embedding OPERATOR(extensions.<=>) query_embedding
    LIMIT k;
END $$;

-- Bloqueo explícito: no depender de los privilegios por defecto del proyecto Supabase.
DO $$
DECLARE t TEXT; fn TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['submission_versions', 'rag_chunks', 'ai_reviews', 'ai_observations', 'ai_audit_log'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON TABLE public.%I FROM PUBLIC, anon, authenticated, service_role', t);
    END LOOP;
    FOREACH fn IN ARRAY ARRAY['validate_ai_review_confirmation()', 'validate_ai_observation_citation()',
        'validate_rag_chunk_owner()', 'next_version_n(uuid)',
        'match_rag_chunks(extensions.vector,uuid[],integer)'] LOOP
        EXECUTE format('REVOKE ALL ON FUNCTION public.%s FROM PUBLIC, anon, authenticated', fn);
        EXECUTE format('GRANT EXECUTE ON FUNCTION public.%s TO service_role', fn);
    END LOOP;
END $$;
GRANT SELECT ON public.submission_versions, public.rag_chunks, public.ai_reviews, public.ai_observations, public.ai_audit_log TO authenticated;
GRANT UPDATE (estado, nota_final, confirmada_por, confirmada_en) ON public.ai_reviews TO authenticated;
GRANT UPDATE (estado_docente, texto) ON public.ai_observations TO authenticated;
-- Las versiones son inmutables: extraer el texto antes de insertar, o insertar otra versión.
GRANT SELECT, INSERT, DELETE ON public.submission_versions TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.rag_chunks, public.ai_reviews, public.ai_observations TO service_role;
GRANT SELECT, INSERT ON public.ai_audit_log TO service_role;
GRANT EXECUTE ON FUNCTION public.match_rag_chunks(extensions.vector, UUID[], INTEGER) TO authenticated;

DROP POLICY IF EXISTS ia_versions_select ON public.submission_versions;
CREATE POLICY ia_versions_select ON public.submission_versions FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM public.activity_submissions s WHERE s.id = submission_id
        AND (public.is_section_staff(public.section_of_activity(s.activity_id)) OR s.student_id = public.current_student_id()))
);

DROP POLICY IF EXISTS ia_chunks_select ON public.rag_chunks;
CREATE POLICY ia_chunks_select ON public.rag_chunks FOR SELECT TO authenticated USING (
    (owner_type = 'version' AND EXISTS (SELECT 1 FROM public.submission_versions v WHERE v.id = owner_id))
    OR (owner_type = 'rubric' AND EXISTS (SELECT 1 FROM public.activities a WHERE a.id = owner_id
        AND (public.is_section_staff(a.section_id) OR (a.is_published AND public.is_section_member(a.section_id)))))
    OR (owner_type = 'course_material' AND EXISTS (SELECT 1 FROM public.sections s
        WHERE s.course_id = owner_id AND public.is_section_member(s.id)))
);

-- La tabla completa contiene notas preliminares: ningún estudiante tiene filas visibles.
DROP POLICY IF EXISTS ia_reviews_select_staff ON public.ai_reviews;
CREATE POLICY ia_reviews_select_staff ON public.ai_reviews FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE v.id = version_id AND public.is_section_staff(public.section_of_activity(s.activity_id)))
);
DROP POLICY IF EXISTS ia_reviews_update_teacher ON public.ai_reviews;
CREATE POLICY ia_reviews_update_teacher ON public.ai_reviews FOR UPDATE TO authenticated USING (
    EXISTS (SELECT 1 FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE v.id = version_id AND public.is_section_teacher(public.section_of_activity(s.activity_id)))
) WITH CHECK (
    EXISTS (SELECT 1 FROM public.submission_versions v JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE v.id = version_id AND public.is_section_teacher(public.section_of_activity(s.activity_id)))
);

DROP POLICY IF EXISTS ia_observations_select_staff ON public.ai_observations;
CREATE POLICY ia_observations_select_staff ON public.ai_observations FOR SELECT TO authenticated USING (
    EXISTS (SELECT 1 FROM public.ai_reviews r JOIN public.submission_versions v ON v.id = r.version_id
        JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE r.id = review_id AND public.is_section_staff(public.section_of_activity(s.activity_id)))
);
DROP POLICY IF EXISTS ia_observations_update_teacher ON public.ai_observations;
CREATE POLICY ia_observations_update_teacher ON public.ai_observations FOR UPDATE TO authenticated USING (
    EXISTS (SELECT 1 FROM public.ai_reviews r JOIN public.submission_versions v ON v.id = r.version_id
        JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE r.id = review_id AND public.is_section_teacher(public.section_of_activity(s.activity_id)))
) WITH CHECK (
    EXISTS (SELECT 1 FROM public.ai_reviews r JOIN public.submission_versions v ON v.id = r.version_id
        JOIN public.activity_submissions s ON s.id = v.submission_id
        WHERE r.id = review_id AND public.is_section_teacher(public.section_of_activity(s.activity_id)))
);

DROP POLICY IF EXISTS ia_audit_select_staff ON public.ai_audit_log;
CREATE POLICY ia_audit_select_staff ON public.ai_audit_log FOR SELECT TO authenticated
    USING (public.is_section_staff(section_id));

COMMIT;

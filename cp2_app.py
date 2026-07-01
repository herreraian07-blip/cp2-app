"""CP2 Maintenance System - SAL DE ORO · POSCO Argentina"""
import streamlit as st
import sqlite3, hashlib, pandas as pd, plotly.express as px, plotly.graph_objects as go
from pathlib import Path
from datetime import datetime, date, timedelta
import os, re, json, gzip

# ── CONFIG ────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="CP2 · SAL DE ORO", page_icon="⚙", layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("""<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif !important}
#MainMenu,footer,header,.stDeployButton,[data-testid="stToolbar"],
[data-testid="stSidebar"],[data-testid="stSidebarCollapseButton"],
[data-testid="collapsedControl"]{display:none !important}
.stApp{background:#0f0f10 !important}
.block-container{padding:1rem 2rem 2rem 2rem !important;max-width:100% !important}
.stButton>button{font-family:'Inter',sans-serif}
div[data-testid="metric-container"]{background:#111113;border:1px solid #1e1e22;
    border-radius:8px;padding:12px 16px}
</style>""", unsafe_allow_html=True)

# ── DATABASE ──────────────────────────────────────────────────────────────────
DB_PATH = Path(__file__).parent / "cp2_planta.db"
SUPA_URL = os.environ.get("SUPABASE_URL","")
SUPA_PASS = os.environ.get("SUPABASE_DB_PASSWORD","")
USE_PG = bool(SUPA_URL and SUPA_PASS)

if USE_PG:
    import psycopg2
    from psycopg2 import pool as _pool

    def _dsn():
        pid = SUPA_URL.replace("https://","").replace(".supabase.co","")
        return (f"postgresql://postgres.{pid}:{SUPA_PASS}"
                f"@aws-1-sa-east-1.pooler.supabase.com:5432/postgres?sslmode=require")

    @st.cache_resource
    def _pg_pool():
        return psycopg2.pool.ThreadedConnectionPool(1, 5, _dsn())

    def _conn():
        return _pg_pool().getconn()

    def _release(c):
        try: _pg_pool().putconn(c)
        except: 
            try: c.close()
            except: pass

    def _fix(sql):
        """SQLite → PostgreSQL"""
        def _sf(m):
            fmt = m.group(1)
            col = m.group(2).strip()
            pg = (fmt.replace('%Y-%m-%d %H:%M','YYYY-MM-DD HH24:MI')
                     .replace('%Y-%m-%d','YYYY-MM-DD')
                     .replace('%Y-%m','YYYY-MM')
                     .replace('%Y%m','YYYYMM')
                     .replace('%d/%m/%Y','DD/MM/YYYY')
                     .replace('%d/%m','DD/MM')
                     .replace('%Y','YYYY').replace('%m','MM').replace('%d','DD'))
            if col.strip("'\"") == 'now':
                return "TO_CHAR(NOW(),'{}')".format(pg)
            return "TO_CHAR({}::timestamp,'{}')".format(col,pg)
        pat = r"strftime\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*([^)]+)\)"
        sql = re.sub(pat, _sf, sql)
        sql = sql.replace("date('now')","CURRENT_DATE::TEXT")
        sql = sql.replace("datetime('now')","NOW()::TEXT")
        sql = sql.replace("?","%s")
        return sql

    def qdf(sql, params=()):
        pg = _fix(sql)
        c = _conn()
        try: return pd.read_sql_query(pg, c, params=list(params))
        finally: _release(c)

    def run(sql, params=()):
        pg = _fix(sql)
        c = _conn()
        try:
            with c.cursor() as cur: cur.execute(pg, list(params))
            c.commit()
        except Exception as e:
            c.rollback(); raise e
        finally: _release(c)

    def run_return(sql, params=()):
        pg = _fix(sql) + " RETURNING id"
        c = _conn()
        try:
            with c.cursor() as cur:
                cur.execute(pg, list(params))
                result = cur.fetchone()
            c.commit()
            return result[0] if result else None
        except Exception as e:
            c.rollback(); raise e
        finally: _release(c)

else:
    def _conn():
        c = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def qdf(sql, params=()):
        return pd.read_sql_query(sql, _conn(), params=params)

    def run(sql, params=()):
        c = _conn(); c.execute(sql, params); c.commit()

    def run_return(sql, params=()):
        c = _conn()
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid

# ── SEED SUPABASE ─────────────────────────────────────────────────────────────
def seed_supabase():
    if not USE_PG: return
    try:
        flag = qdf("SELECT valor FROM configuracion WHERE clave='seed_completed'")
        if len(flag) > 0 and str(flag.iloc[0]["valor"]) == "1": return
    except: pass

    seed_file = Path(__file__).parent / "seed_data.json.gz"
    if not seed_file.exists():
        st.warning("seed_data.json.gz no encontrado")
        return

    with gzip.open(seed_file,'rt',encoding='utf-8') as f:
        all_data = json.load(f)

    tables = list(all_data.keys())
    prog = st.progress(0, text="Iniciando carga de datos...")

    for i, table in enumerate(tables):
        cols = all_data[table]["cols"]
        rows = all_data[table]["rows"]
        if not rows:
            continue
        try:
            check = qdf(f"SELECT COUNT(*) as n FROM {table}")
            if check.iloc[0]["n"] > 0:
                prog.progress((i+1)/len(tables), text=f"✓ {table}")
                continue
        except: pass

        cols_str = ", ".join(cols)
        ph = ", ".join(["%s"]*len(cols))
        sql = f"INSERT INTO {table} ({cols_str}) VALUES ({ph}) ON CONFLICT DO NOTHING"
        c = _conn()
        try:
            with c.cursor() as cur:
                for j in range(0, len(rows), 20):
                    cur.executemany(sql, rows[j:j+20])
            c.commit()
        except Exception as e:
            c.rollback()
            st.warning(f"Error en {table}: {e}")
        finally: _release(c)
        prog.progress((i+1)/len(tables), text=f"Cargando {table} ({len(rows)} registros)...")

    try:
        run("INSERT INTO configuracion (clave,valor,descripcion) VALUES (?,?,?) ON CONFLICT (clave) DO UPDATE SET valor=?",
            ("seed_completed","1","Seed inicial","1"))
    except: pass

    prog.progress(1.0, text="✅ Listo")
    import time; time.sleep(1)
    prog.empty()
    st.rerun()

seed_supabase()

# ── HELPERS ───────────────────────────────────────────────────────────────────
def pw_hash(p): return hashlib.sha256(p.encode()).hexdigest()

def fmt_fecha(v):
    if not v or str(v) in ('None','nan',''): return "—"
    return str(v)[:10]

# ── SESION ────────────────────────────────────────────────────────────────────
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.user_id = None
    st.session_state.username = ""
    st.session_state.nombre = ""
    st.session_state.apellido = ""
    st.session_state.rol = ""
    st.session_state.lang = "es"
    st.session_state.pagina = "dashboard"

LANG = st.session_state.lang

# ── TRADUCCIONES ──────────────────────────────────────────────────────────────
T = {
    # Nav
    "dashboard":   {"es":"Dashboard",        "en":"Dashboard",        "ko":"대시보드"},
    "equipos":     {"es":"Equipos",          "en":"Equipment",        "ko":"설비"},
    "ots":         {"es":"OTs",              "en":"Work Orders",      "ko":"작업지시"},
    "mediciones":  {"es":"Mediciones",       "en":"Measurements",     "ko":"측정"},
    "pr_nav":      {"es":"Compras",          "en":"Purchases",        "ko":"구매"},
    "repuestos_nav":{"es":"Repuestos Q",     "en":"Q Spares",         "ko":"Q 부품"},
    "mapa":        {"es":"Mapa",             "en":"Map",              "ko":"지도"},
    "plan_nav":    {"es":"Plan Prev.",       "en":"PM Plan",          "ko":"예방정비"},
    "paradas_nav": {"es":"Paradas",          "en":"Shutdowns",        "ko":"정지"},
    "buzon_nav":   {"es":"Buzón",            "en":"Mailbox",          "ko":"제안함"},
    # Auth
    "ingresar":    {"es":"Ingresar",         "en":"Sign in",          "ko":"로그인"},
    "usuario":     {"es":"Usuario",          "en":"Username",         "ko":"사용자명"},
    "contrasena":  {"es":"Contraseña",       "en":"Password",         "ko":"비밀번호"},
    "err_login":   {"es":"Usuario o contraseña incorrectos",
                    "en":"Incorrect username or password",
                    "ko":"잘못된 사용자명 또는 비밀번호"},
    # General
    "buscar":      {"es":"Buscar...",        "en":"Search...",        "ko":"검색..."},
    "guardar":     {"es":"Guardar",          "en":"Save",             "ko":"저장"},
    "agregar":     {"es":"Agregar",          "en":"Add",              "ko":"추가"},
    "cancelar":    {"es":"Cancelar",         "en":"Cancel",           "ko":"취소"},
    "estado":      {"es":"Estado",           "en":"Status",           "ko":"상태"},
    "fecha":       {"es":"Fecha",            "en":"Date",             "ko":"날짜"},
    "area":        {"es":"Área",             "en":"Area",             "ko":"구역"},
    "todos":       {"es":"Todos",            "en":"All",              "ko":"전체"},
    "sin_datos":   {"es":"Sin datos",        "en":"No data",          "ko":"데이터 없음"},
    "actualizar":  {"es":"Actualizar desde archivo","en":"Update from file","ko":"파일로 업데이트"},
    "ok_update":   {"es":"Datos actualizados","en":"Data updated","ko":"데이터 업데이트됨"},
    # Mediciones
    "mecanicas":   {"es":"⚙ Mecánicas",     "en":"⚙ Mechanical",    "ko":"⚙ 기계"},
    "electricas":  {"es":"⚡ Eléctricas",   "en":"⚡ Electrical",   "ko":"⚡ 전기"},
    "vib":         {"es":"Vibraciones",      "en":"Vibrations",       "ko":"진동"},
    "ht":          {"es":"Heat Trace",       "en":"Heat Trace",       "ko":"히트 트레이스"},
    "plc":         {"es":"PLC Diario",       "en":"Daily PLC",        "ko":"일일 PLC"},
    "semanal":     {"es":"Inspección Semanal","en":"Weekly Inspection","ko":"주간 점검"},
    # OT
    "nueva_ot":    {"es":"Nueva OT",         "en":"New WO",           "ko":"새 작업지시"},
    "tipo_tarea":  {"es":"Tipo de tarea",    "en":"Task type",        "ko":"작업 유형"},
    "correctivo":  {"es":"Correctivo",       "en":"Corrective",       "ko":"사후정비"},
    "preventivo":  {"es":"Preventivo",       "en":"Preventive",       "ko":"예방정비"},
    "inspeccion":  {"es":"Inspección",       "en":"Inspection",       "ko":"점검"},
    "pendiente":   {"es":"Pendiente",        "en":"Pending",          "ko":"대기"},
    "en_curso":    {"es":"En curso",         "en":"In progress",      "ko":"진행중"},
    "completada":  {"es":"Completada",       "en":"Completed",        "ko":"완료"},
}

def t(k): return T.get(k,{}).get(LANG, T.get(k,{}).get("es",k))

# ── LOGIN ─────────────────────────────────────────────────────────────────────
if not st.session_state.logged_in:
    col_l, col_c, col_r = st.columns([1,2,1])
    with col_c:
        st.markdown("## CP2 · SAL DE ORO")
        st.markdown("##### Sistema de Mantenimiento — POSCO Argentina")
        st.markdown("---")

        # Language selector
        lc1,lc2,lc3 = st.columns(3)
        with lc1:
            if st.button("🇦🇷 Español", use_container_width=True,
                         type="primary" if LANG=="es" else "secondary", key="ll_es"):
                st.session_state.lang="es"; st.rerun()
        with lc2:
            if st.button("🇬🇧 English", use_container_width=True,
                         type="primary" if LANG=="en" else "secondary", key="ll_en"):
                st.session_state.lang="en"; st.rerun()
        with lc3:
            if st.button("🇰🇷 한국어", use_container_width=True,
                         type="primary" if LANG=="ko" else "secondary", key="ll_ko"):
                st.session_state.lang="ko"; st.rerun()

        st.markdown("")
        with st.form("form_login"):
            usr = st.text_input(t("usuario"), key="li_usr")
            pwd = st.text_input(t("contrasena"), type="password", key="li_pwd")
            if st.form_submit_button(t("ingresar"), type="primary", use_container_width=True):
                if usr and pwd:
                    h = pw_hash(pwd)
                    try:
                        u = qdf("SELECT * FROM usuarios WHERE username=? AND activo=1", (usr.strip(),))
                        if len(u) > 0 and u.iloc[0]["password_hash"] == h:
                            row = u.iloc[0]
                            st.session_state.logged_in = True
                            st.session_state.user_id = int(row["id"])
                            st.session_state.username = row["username"]
                            st.session_state.nombre = row["nombre"]
                            st.session_state.apellido = row["apellido"]
                            st.session_state.rol = row["rol"]
                            if row.get("idioma"):
                                st.session_state.lang = row["idioma"]
                            try:
                                run("UPDATE usuarios SET last_login=? WHERE id=?",
                                    (datetime.now().strftime("%Y-%m-%d %H:%M"), int(row["id"])))
                            except: pass
                            st.rerun()
                        else:
                            st.error(t("err_login"))
                    except Exception as e:
                        st.error(f"Error: {e}")
                else:
                    st.error(t("err_login"))
    st.stop()

# ── TOPBAR ────────────────────────────────────────────────────────────────────
NOMBRE = st.session_state.nombre
APELLIDO = st.session_state.apellido
ROL = st.session_state.rol
UID = st.session_state.user_id
INICIALES = (NOMBRE[:1] + APELLIDO[:1]).upper()
PUEDE_EDITAR = ROL in ("admin","supervisor","tecnico")
PUEDE_ADMIN = ROL == "admin"

tb1, tb2, tb3, tb4, tb5 = st.columns([5,1,1,1,3])
with tb1:
    st.markdown("""<div style="padding:10px 0;font-size:15px;font-weight:700;color:#fff">
        CP2 · SAL DE ORO <span style="font-size:11px;color:#444;font-weight:400;margin-left:8px">POSCO Argentina</span>
    </div>""", unsafe_allow_html=True)
with tb2:
    if st.button("🇦🇷", use_container_width=True,
                 type="primary" if LANG=="es" else "secondary", key="lang_es"):
        st.session_state.lang="es"; st.rerun()
with tb3:
    if st.button("🇬🇧", use_container_width=True,
                 type="primary" if LANG=="en" else "secondary", key="lang_en"):
        st.session_state.lang="en"; st.rerun()
with tb4:
    if st.button("🇰🇷", use_container_width=True,
                 type="primary" if LANG=="ko" else "secondary", key="lang_ko"):
        st.session_state.lang="ko"; st.rerun()
with tb5:
    c_u1,c_u2 = st.columns([1,4])
    with c_u1:
        st.markdown(f"""<div style="width:30px;height:30px;border-radius:50%;background:#2563eb;
            display:flex;align-items:center;justify-content:center;font-size:11px;
            font-weight:600;color:#fff;margin-top:6px">{INICIALES}</div>""",
            unsafe_allow_html=True)
    with c_u2:
        st.markdown(f"""<div style="padding:6px 0;font-size:12px;color:#aaa">
            {NOMBRE} {APELLIDO}<br>
            <span style="font-size:10px;color:#555;border:1px solid #222;
            border-radius:3px;padding:1px 5px">{ROL}</span></div>""",
            unsafe_allow_html=True)

# ── NAVEGACIÓN ────────────────────────────────────────────────────────────────
NAV = ["dashboard","equipos","ots","mediciones","pr_nav",
       "repuestos_nav","mapa","plan_nav","paradas_nav","buzon_nav"]
NAV_PAGE = {"dashboard":"dashboard","equipos":"equipos","ots":"ots",
            "mediciones":"mediciones","pr_nav":"pr","repuestos_nav":"repuestos",
            "mapa":"mapa","plan_nav":"plan","paradas_nav":"paradas","buzon_nav":"buzon"}

nav_cols = st.columns(len(NAV))
for i, key in enumerate(NAV):
    with nav_cols[i]:
        target = NAV_PAGE[key]
        active = st.session_state.pagina == target
        if st.button(t(key), key=f"nav_{key}", use_container_width=True,
                     type="primary" if active else "secondary"):
            st.session_state.pagina = target
            st.rerun()

st.markdown("<hr style='border-color:#1e1e22;margin:8px 0 16px 0'>", unsafe_allow_html=True)

pagina = st.session_state.pagina

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
if pagina == "dashboard":
    LANG_D = st.session_state.lang

    # Métricas principales
    try:
        eq = qdf("SELECT motor_kw, mec_doc_numero FROM equipos")
        n_eq = len(eq)
        n_motor = eq["motor_kw"].notna().sum()
        kw = pd.to_numeric(eq["motor_kw"], errors="coerce").fillna(0).sum()
        n_ds = eq["mec_doc_numero"].notna().sum()
    except:
        n_eq=n_motor=kw=n_ds=0

    try:
        ots_activas = qdf("SELECT COUNT(*) n FROM ordenes_trabajo WHERE estado IN ('pendiente','en_curso')")
        oa = int(ots_activas.iloc[0]["n"])
    except: oa=0

    try:
        n_criticos = qdf("SELECT COUNT(DISTINCT tag) n FROM vibraciones WHERE estado='critico'")
        nc = int(n_criticos.iloc[0]["n"])
    except: nc=0

    c1,c2,c3,c4,c5 = st.columns(5)
    with c1: st.metric("Equipos registrados", f"{n_eq:,}")
    with c2: st.metric("Con datos de motor", n_motor)
    with c3: st.metric("Potencia instalada", f"{round(kw):,} kW")
    with c4: st.metric("OTs activas", oa)
    with c5: st.metric("🔴 Vibraciones críticas", nc)

    st.markdown("---")

    # ── PANEL VIBRACIONES CRÍTICAS ────────────────────────────────────────────
    try:
        vib_crit = qdf("""
            SELECT tag, equipo_desc, MAX(valor) val, limite
            FROM vibraciones WHERE estado='critico'
            GROUP BY tag ORDER BY MAX(valor) DESC
        """)

        if len(vib_crit) > 0:
            badges = "".join([
                f"<span style='background:#1a0505;border:1px solid #7f1d1d;border-radius:5px;"
                f"padding:4px 10px;font-family:monospace;font-size:11px;color:#fca5a5;"
                f"display:inline-block;margin:2px'><b>{r['tag']}</b>&nbsp;{round(float(r['val']),1)} mm/s</span>"
                for _,r in vib_crit.iterrows()])

            st.markdown(f"""
            <div style="background:linear-gradient(135deg,#1a0505,#2a0808);
                border:1.5px solid #ef4444;border-radius:10px;padding:14px 18px;
                margin-bottom:16px;box-shadow:0 0 20px rgba(239,68,68,0.15)">
                <div style="font-size:14px;font-weight:700;color:#ef4444;margin-bottom:10px">
                    🚨 {len(vib_crit)} equipos con vibración CRÍTICA</div>
                <div style="display:flex;flex-wrap:wrap;gap:4px">{badges}</div>
            </div>""", unsafe_allow_html=True)

            # Gráfico rotativo
            import json as _json
            tags_sql = ",".join([f"'{r}'" for r in vib_crit["tag"].tolist()])
            vib_trend = qdf(f"""
                SELECT tag, equipo_desc, fecha, MAX(valor) valor, limite
                FROM vibraciones
                WHERE tag IN ({tags_sql}) AND tipo_medicion LIKE '%Vib%'
                GROUP BY tag, fecha ORDER BY tag, fecha
            """)
            vib_trend["fecha"] = pd.to_datetime(vib_trend["fecha"], errors="coerce")

            traces = []
            for _,r in vib_crit.iterrows():
                df_t = vib_trend[vib_trend.tag==r["tag"]].sort_values("fecha")
                if len(df_t)==0: continue
                traces.append({
                    "tag": r["tag"],
                    "nombre": str(df_t["equipo_desc"].iloc[0])[:35],
                    "fechas": df_t["fecha"].dt.strftime("%d/%m").tolist(),
                    "vals": [round(float(v),2) for v in df_t["valor"].tolist()],
                    "limite": float(r["limite"]),
                    "max_v": round(float(r["val"]),2),
                })

            if traces:
                import streamlit.components.v1 as _components
                charts_json = _json.dumps(traces)
                html_vib = f"""<!DOCTYPE html><html><head>
                <script src="https://cdn.plot.ly/plotly-2.26.0.min.js"></script>
                <style>body{{margin:0;padding:8px;background:transparent;font-family:Inter,Arial,sans-serif}}
                #chart{{width:100%;height:240px}}
                #nav{{display:flex;justify-content:center;gap:6px;margin-top:6px;flex-wrap:wrap}}
                .dot{{width:8px;height:8px;border-radius:50%;background:#333;cursor:pointer;
                      border:1px solid #444;transition:all 0.2s}}
                .dot.active{{background:#ef4444;border-color:#ef4444;transform:scale(1.3)}}
                #lbl{{text-align:center;font-size:11px;color:#666;margin-top:3px;font-family:monospace}}
                </style></head><body>
                <div id="chart"></div><div id="lbl"></div><div id="nav"></div>
                <script>
                var D={charts_json},cur=0,tmr=null;
                function render(i){{
                  var d=D[i],clrs=d.vals.map(v=>v>d.limite?'#ef4444':v>d.limite*0.8?'#f59e0b':'#22c55e');
                  Plotly.newPlot('chart',[
                    {{x:d.fechas,y:d.vals,type:'scatter',mode:'lines+markers',
                      line:{{color:'#6366f1',width:2}},
                      marker:{{color:clrs,size:5}},
                      hovertemplate:'%{{x}}: <b>%{{y}}</b> mm/s<extra></extra>'}},
                    {{x:[d.fechas[0],d.fechas[d.fechas.length-1]],y:[d.limite,d.limite],
                      type:'scatter',mode:'lines',
                      line:{{color:'#ef4444',width:1.5,dash:'dash'}},
                      name:'Límite '+d.limite,hoverinfo:'skip'}}
                  ],{{paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',
                      margin:{{l:45,r:15,t:28,b:30}},
                      title:{{text:d.nombre,font:{{color:'#aaa',size:12}},x:0.5}},
                      xaxis:{{color:'#444',gridcolor:'#1e1e22',tickfont:{{size:9,color:'#555'}}}},
                      yaxis:{{color:'#444',gridcolor:'#1e1e22',title:{{text:'mm/s',font:{{size:10}}}}}},
                      legend:{{font:{{color:'#555',size:9}},bgcolor:'rgba(0,0,0,0)',
                               orientation:'h',y:-0.18}},showlegend:true}},
                  {{displayModeBar:false,responsive:true}});
                  document.getElementById('lbl').innerText=
                    d.tag+' · max: '+Math.max(...d.vals).toFixed(1)+' mm/s · límite: '+d.limite;
                  document.querySelectorAll('.dot').forEach((dt,j)=>
                    dt.classList.toggle('active',j===i));
                  cur=i;
                }}
                var nav=document.getElementById('nav');
                D.forEach((d,i)=>{{
                  var dt=document.createElement('div');
                  dt.className='dot'+(i===0?' active':'');
                  dt.title=d.tag+' (max: '+d.max_v+' mm/s)';
                  dt.onclick=function(){{clearInterval(tmr);render(i);tmr=setInterval(()=>render((cur+1)%D.length),15000);}};
                  nav.appendChild(dt);
                }});
                if(D.length>0){{render(0);tmr=setInterval(()=>render((cur+1)%D.length),15000);}}
                </script></body></html>"""
                _components.html(html_vib, height=330, scrolling=False)
        else:
            st.success("✅ Sin equipos con vibración crítica")
    except Exception as _e_vib:
        st.warning(f"Vibraciones: {_e_vib}")

    st.markdown("---")

    # Panel vibraciones críticas
    try:
        vib_crit = qdf("""
            SELECT tag, equipo_desc, MAX(valor) val, limite
            FROM vibraciones WHERE estado='critico'
            GROUP BY tag ORDER BY MAX(valor) DESC
        """)

        if len(vib_crit) > 0:
            titulo_vib = {
                "es": f"🚨 {len(vib_crit)} equipos con vibración CRÍTICA",
                "en": f"🚨 {len(vib_crit)} equipment with CRITICAL vibration",
                "ko": f"🚨 {len(vib_crit)}개 장비 임계 진동"
            }.get(LANG_D,"")

            badges = "".join([
                f"<span style='background:#1a0505;border:1px solid #7f1d1d;border-radius:5px;"
                f"padding:4px 10px;font-family:monospace;font-size:11px;color:#fca5a5;"
                f"display:inline-block;margin:2px'><b>{r['tag']}</b>&nbsp;{round(float(r['val']),1)} mm/s</span>"
                for _,r in vib_crit.iterrows()])

            st.markdown(f"""
            <div style="background:linear-gradient(135deg,#1a0505,#2a0808);
                border:1.5px solid #ef4444;border-radius:10px;padding:14px 18px;
                margin-bottom:16px;box-shadow:0 0 20px rgba(239,68,68,0.15)">
                <div style="font-size:14px;font-weight:700;color:#ef4444;margin-bottom:10px">
                    {titulo_vib}</div>
                <div style="display:flex;flex-wrap:wrap;gap:4px">{badges}</div>
            </div>""", unsafe_allow_html=True)

            # Gráfico rotativo
            tags_sql = ",".join([f"'{r}'" for r in vib_crit["tag"].tolist()])
            vib_trend = qdf(f"""
                SELECT tag, equipo_desc, fecha, MAX(valor) valor, limite
                FROM vibraciones
                WHERE tag IN ({tags_sql}) AND tipo_medicion LIKE '%Vib%'
                GROUP BY tag, fecha ORDER BY tag, fecha
            """)
            vib_trend["fecha"] = pd.to_datetime(vib_trend["fecha"], errors="coerce")

            traces = []
            for _,r in vib_crit.iterrows():
                df_t = vib_trend[vib_trend.tag==r["tag"]].sort_values("fecha")
                if len(df_t)==0: continue
                traces.append({
                    "tag": r["tag"],
                    "nombre": str(df_t["equipo_desc"].iloc[0])[:35],
                    "fechas": df_t["fecha"].dt.strftime("%d/%m").tolist(),
                    "vals": [round(float(v),2) for v in df_t["valor"].tolist()],
                    "limite": float(r["limite"]),
                    "max_v": round(float(r["val"]),2),
                })

            if traces:
                import streamlit.components.v1 as components
                charts_json = json.dumps(traces)
                html = f"""<!DOCTYPE html><html><head>
                <script src="https://cdn.plot.ly/plotly-2.26.0.min.js"></script>
                <style>body{{margin:0;padding:8px;background:transparent;font-family:Inter,Arial,sans-serif}}
                #chart{{width:100%;height:240px}}
                #nav{{display:flex;justify-content:center;gap:6px;margin-top:6px;flex-wrap:wrap}}
                .dot{{width:8px;height:8px;border-radius:50%;background:#333;cursor:pointer;
                      border:1px solid #444;transition:all 0.2s}}
                .dot.active{{background:#ef4444;border-color:#ef4444;transform:scale(1.3)}}
                #lbl{{text-align:center;font-size:11px;color:#666;margin-top:3px;font-family:monospace}}
                </style></head><body>
                <div id="chart"></div><div id="lbl"></div><div id="nav"></div>
                <script>
                var D={charts_json},cur=0,tmr=null;
                function render(i){{
                  var d=D[i],clrs=d.vals.map(v=>v>d.limite?'#ef4444':v>d.limite*0.8?'#f59e0b':'#22c55e');
                  Plotly.newPlot('chart',[
                    {{x:d.fechas,y:d.vals,type:'scatter',mode:'lines+markers',
                      line:{{color:'#6366f1',width:2}},
                      marker:{{color:clrs,size:5}},
                      hovertemplate:'%{{x}}: <b>%{{y}}</b> mm/s<extra></extra>'}},
                    {{x:[d.fechas[0],d.fechas[d.fechas.length-1]],y:[d.limite,d.limite],
                      type:'scatter',mode:'lines',
                      line:{{color:'#ef4444',width:1.5,dash:'dash'}},
                      name:'Límite '+d.limite,hoverinfo:'skip'}}
                  ],{{paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',
                      margin:{{l:45,r:15,t:28,b:30}},
                      title:{{text:d.nombre,font:{{color:'#aaa',size:12}},x:0.5}},
                      xaxis:{{color:'#444',gridcolor:'#1e1e22',tickfont:{{size:9,color:'#555'}}}},
                      yaxis:{{color:'#444',gridcolor:'#1e1e22',title:{{text:'mm/s',font:{{size:10}}}}}},
                      legend:{{font:{{color:'#555',size:9}},bgcolor:'rgba(0,0,0,0)',
                               orientation:'h',y:-0.18}},showlegend:true}},
                  {{displayModeBar:false,responsive:true}});
                  document.getElementById('lbl').innerText=
                    d.tag+' · max: '+Math.max(...d.vals).toFixed(1)+' mm/s · límite: '+d.limite;
                  document.querySelectorAll('.dot').forEach((dt,j)=>
                    dt.classList.toggle('active',j===i));
                  cur=i;
                }}
                var nav=document.getElementById('nav');
                D.forEach((d,i)=>{{
                  var dt=document.createElement('div');
                  dt.className='dot'+(i===0?' active':'');
                  dt.title=d.tag+' (max: '+d.max_v+' mm/s)';
                  dt.onclick=function(){{clearInterval(tmr);render(i);tmr=setInterval(()=>render((cur+1)%D.length),15000);}};
                  nav.appendChild(dt);
                }});
                if(D.length>0){{render(0);tmr=setInterval(()=>render((cur+1)%D.length),15000);}}
                </script></body></html>"""
                components.html(html, height=330, scrolling=False)
        else:
            st.success("✅ Sin equipos con vibración crítica")
    except Exception as e:
        st.warning(f"Vibraciones: {e}")

    st.markdown("---")

    # OTs recientes y últimas alarmas
    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("#### Últimas OTs")
        try:
            ots = qdf("""SELECT numero_ot, tag_equipo, titulo, estado, fecha_inicio
                        FROM ordenes_trabajo ORDER BY fecha_inicio DESC LIMIT 8""")
            if len(ots) > 0:
                est_color = {"pendiente":"🟡","en_curso":"🔵","completada":"✅","cancelada":"⚫"}
                for _,r in ots.iterrows():
                    ic = est_color.get(str(r.get("estado","")),"⚪")
                    st.markdown(f"""
                    <div style="background:#111113;border:1px solid #1e1e22;border-radius:6px;
                        padding:8px 12px;margin-bottom:4px;display:flex;align-items:center;gap:8px">
                        <span>{ic}</span>
                        <span style="font-family:monospace;font-size:10px;color:#555">{r.get('numero_ot','')}</span>
                        <span style="font-size:11px;color:#ccc;flex:1">{str(r.get('titulo',''))[:40]}</span>
                        <span style="font-size:10px;color:#444">{fmt_fecha(r.get('fecha_inicio'))}</span>
                    </div>""", unsafe_allow_html=True)
            else:
                st.info(t("sin_datos"))
        except Exception as e:
            st.warning(f"OTs: {e}")

    with col_b:
        st.markdown("#### PRs activas")
        try:
            prs = qdf("""SELECT solicitud, titulo, estado, solicitante
                        FROM purchase_requests
                        WHERE estado NOT IN ('Delivered','Cancelled')
                        ORDER BY id DESC LIMIT 8""")
            if len(prs) > 0:
                est_clr = {"In progress":"🔵","Pending":"🟡","In transit":"🟠"}
                for _,r in prs.iterrows():
                    ic = est_clr.get(str(r.get("estado","")),"⚪")
                    st.markdown(f"""
                    <div style="background:#111113;border:1px solid #1e1e22;border-radius:6px;
                        padding:8px 12px;margin-bottom:4px;display:flex;align-items:center;gap:8px">
                        <span>{ic}</span>
                        <span style="font-size:11px;color:#ccc;flex:1">{str(r.get('titulo',''))[:40]}</span>
                        <span style="font-size:10px;color:#555">{r.get('solicitante','')}</span>
                    </div>""", unsafe_allow_html=True)
            else:
                st.info(t("sin_datos"))
        except Exception as e:
            st.warning(f"PRs: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# EQUIPOS
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "equipos":
    st.markdown(f"## {t('equipos')}")

    c1,c2,c3 = st.columns([3,2,2])
    with c1:
        busq = st.text_input(" ", placeholder=t("buscar"), label_visibility="collapsed", key="eq_busq")
    with c2:
        areas_df = qdf("SELECT DISTINCT area_codigo, area_descripcion FROM equipos WHERE area_codigo IS NOT NULL ORDER BY area_codigo")
        area_opts = ["Todas"] + [f"{r['area_codigo']} — {r['area_descripcion'] or ''}" for _,r in areas_df.iterrows()]
        area_sel = st.selectbox(" ", area_opts, label_visibility="collapsed", key="eq_area")
    with c3:
        tipo_opts = ["Todos"] + sorted(qdf("SELECT DISTINCT tipo_codigo FROM equipos WHERE tipo_codigo IS NOT NULL ORDER BY tipo_codigo")["tipo_codigo"].tolist())
        tipo_sel = st.selectbox(" ", tipo_opts, label_visibility="collapsed", key="eq_tipo")

    # Build query
    where = []
    params = []
    if busq:
        where.append("(tag LIKE ? OR spec_nombre_equipo LIKE ? OR tipo_descripcion LIKE ?)")
        params += [f"%{busq}%"]*3
    if area_sel != "Todas":
        ac = area_sel.split(" — ")[0]
        where.append("area_codigo=?"); params.append(ac)
    if tipo_sel != "Todos":
        where.append("tipo_codigo=?"); params.append(tipo_sel)

    sql = "SELECT tag,area_codigo,tipo_codigo,tipo_descripcion,spec_nombre_equipo,motor_kw,mec_doc_numero FROM equipos"
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY tag LIMIT 200"

    eq_df = qdf(sql, tuple(params))
    st.caption(f"{len(eq_df)} equipos")

    # Tabla
    cols_show = ["tag","area_codigo","tipo_codigo","spec_nombre_equipo","motor_kw","mec_doc_numero"]
    cols_show = [c for c in cols_show if c in eq_df.columns]
    st.dataframe(eq_df[cols_show].rename(columns={
        "tag":"Tag","area_codigo":"Área","tipo_codigo":"Tipo",
        "spec_nombre_equipo":"Nombre","motor_kw":"kW","mec_doc_numero":"Doc"}),
        use_container_width=True, hide_index=True, height=300)

    # Ficha técnica
    if len(eq_df) > 0:
        tag_sel = st.selectbox("Ver ficha técnica", ["—"] + eq_df["tag"].tolist(),
                               label_visibility="collapsed", key="eq_ficha_sel")
        if tag_sel != "—":
            eq = qdf("SELECT * FROM equipos WHERE tag=?", (tag_sel,))
            if len(eq) > 0:
                r = eq.iloc[0]
                st.markdown(f"### {tag_sel} — {r.get('spec_nombre_equipo','')}")
                t1,t2,t3,t4 = st.tabs(["General","Mecánico","Motor","Eléctrico/Instr."])

                with t1:
                    c1,c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**Área:** {r.get('area_codigo','')} — {r.get('area_descripcion','')}")
                        st.markdown(f"**Tipo:** {r.get('tipo_codigo','')} — {r.get('tipo_descripcion','')}")
                        st.markdown(f"**Función:** {r.get('spec_funcion','—')}")
                        st.markdown(f"**Capacidad:** {r.get('spec_capacidad','—')}")
                    with c2:
                        st.markdown(f"**Input:** {r.get('spec_input_tipo','—')} desde {r.get('spec_input_desde','—')}")
                        st.markdown(f"**Output:** {r.get('spec_output_tipo','—')} hacia {r.get('spec_output_hacia','—')}")
                        st.markdown(f"**Flujo másico:** {r.get('spec_flujo_masa','—')}")

                with t2:
                    c1,c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**Fabricante:** {r.get('mec_fabricante','—')}")
                        st.markdown(f"**Modelo:** {r.get('mec_modelo','—')}")
                        st.markdown(f"**Capacidad:** {r.get('mec_capacidad','—')}")
                        st.markdown(f"**Presión:** {r.get('mec_presion_bar','—')} bar")
                    with c2:
                        st.markdown(f"**Temperatura:** {r.get('mec_temperatura','—')}")
                        st.markdown(f"**Material:** {r.get('mec_material','—')}")
                        st.markdown(f"**Peso:** {r.get('mec_peso_kg','—')} kg")
                        st.markdown(f"**Norma:** {r.get('mec_norma','—')}")

                with t3:
                    if r.get("motor_kw"):
                        c1,c2,c3 = st.columns(3)
                        with c1:
                            st.metric("Potencia", f"{r.get('motor_kw','—')} kW")
                            st.markdown(f"**Fabricante:** {r.get('motor_fabricante','—')}")
                            st.markdown(f"**Modelo:** {r.get('motor_modelo','—')}")
                        with c2:
                            st.metric("Voltaje", f"{r.get('motor_volt','—')} V")
                            st.metric("FLA", f"{r.get('motor_fla_a','—')} A")
                            st.markdown(f"**RPM:** {r.get('motor_rpm','—')}")
                        with c3:
                            st.metric("PF", f"{r.get('motor_pf_pct','—')} %")
                            st.metric("Eficiencia", f"{r.get('motor_eff_pct','—')} %")
                            st.markdown(f"**Arranque:** {r.get('motor_arranque','—')}")
                    else:
                        st.info("Sin datos de motor")

                with t4:
                    c1,c2 = st.columns(2)
                    with c1:
                        st.markdown(f"**Doc eléctrico:** {r.get('elec_doc_num','—')}")
                        st.markdown(f"**Voltaje:** {r.get('elec_voltaje_v','—')} V")
                        st.markdown(f"**Corriente:** {r.get('elec_corriente_a','—')} A")
                        st.markdown(f"**IP:** {r.get('elec_ip','—')}")
                    with c2:
                        st.markdown(f"**Instrumentos:** {r.get('instr_tags','—')}")

                    # Repuestos Q vinculados
                    tipo_eq = r.get("tipo_codigo","")
                    if tipo_eq:
                        rep_q = qdf("""SELECT r.codigo_q, r.descripcion, r.unidad, r.fabricante
                            FROM repuestos_q r
                            JOIN repuesto_tipo_equipo rte ON r.codigo_q=rte.codigo_q
                            WHERE rte.tipo_equipo=? LIMIT 20""", (tipo_eq,))
                        if len(rep_q) > 0:
                            st.markdown("**Repuestos Q vinculados:**")
                            st.dataframe(rep_q.rename(columns={"codigo_q":"Q","descripcion":"Descripción",
                                "unidad":"Ud","fabricante":"Fabricante"}),
                                use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# ÓRDENES DE TRABAJO
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "ots":
    st.markdown(f"## {t('ots')}")

    tab_lista, tab_nueva = st.tabs(["📋 Lista de OTs", "➕ Nueva OT"])

    with tab_lista:
        c1,c2,c3 = st.columns([3,2,2])
        with c1: busq_ot = st.text_input(" ", placeholder=t("buscar"), label_visibility="collapsed", key="ot_busq")
        with c2:
            est_opts = [t("todos"),"pendiente","en_curso","completada","cancelada"]
            est_sel = st.selectbox(" ", est_opts, label_visibility="collapsed", key="ot_est")
        with c3:
            tipo_ot_opts = [t("todos"),"correctivo","preventivo","inspeccion","lubricacion"]
            tipo_ot_sel = st.selectbox(" ", tipo_ot_opts, label_visibility="collapsed", key="ot_tipo")

        where_ot = []; params_ot = []
        if busq_ot:
            where_ot.append("(numero_ot LIKE ? OR tag_equipo LIKE ? OR titulo LIKE ?)")
            params_ot += [f"%{busq_ot}%"]*3
        if est_sel != t("todos"):
            where_ot.append("estado=?"); params_ot.append(est_sel)
        if tipo_ot_sel != t("todos"):
            where_ot.append("tipo_tarea=?"); params_ot.append(tipo_ot_sel)

        sql_ot = "SELECT * FROM ordenes_trabajo"
        if where_ot: sql_ot += " WHERE " + " AND ".join(where_ot)
        sql_ot += " ORDER BY fecha_creacion DESC LIMIT 100"
        ots_df = qdf(sql_ot, tuple(params_ot))

        st.caption(f"{len(ots_df)} OTs")

        est_color = {"pendiente":"🟡","en_curso":"🔵","completada":"✅","cancelada":"⚫"}
        for _,ot in ots_df.iterrows():
            ic = est_color.get(str(ot.get("estado","")),"⚪")
            with st.expander(f"{ic} {ot.get('numero_ot','')} · {ot.get('tag_equipo','')} · {str(ot.get('titulo',''))[:50]}"):
                c1,c2,c3 = st.columns(3)
                with c1:
                    st.markdown(f"**Tag:** {ot.get('tag_equipo','')}")
                    st.markdown(f"**Tipo:** {ot.get('tipo_tarea','')}")
                    st.markdown(f"**Prioridad:** {ot.get('prioridad','')}")
                with c2:
                    st.markdown(f"**Estado:** {ot.get('estado','')}")
                    st.markdown(f"**Inicio:** {fmt_fecha(ot.get('fecha_inicio'))}")
                    st.markdown(f"**Fin:** {fmt_fecha(ot.get('fecha_fin'))}")
                with c3:
                    st.markdown(f"**Horas est.:** {ot.get('horas_estimadas','—')}")
                    st.markdown(f"**Horas reales:** {ot.get('horas_reales','—')}")
                if ot.get("descripcion"):
                    st.markdown(f"**Descripción:** {ot.get('descripcion')}")
                if ot.get("accion_tomada"):
                    st.markdown(f"**Acción:** {ot.get('accion_tomada')}")

                if PUEDE_EDITAR:
                    st.markdown("---")
                    cu1,cu2,cu3,cu4 = st.columns(4)
                    with cu1:
                        estados_list = ["pendiente","en_curso","completada","cancelada"]
                        est_actual = str(ot.get("estado","pendiente"))
                        idx_est = estados_list.index(est_actual) if est_actual in estados_list else 0 if est_actual in estados_list else 0
                        new_est = st.selectbox("Estado", estados_list, index=idx_est, key=f"ot_est_{ot['id']}")
                    with cu2:
                        new_hrs = st.number_input("Horas reales", min_value=0.0, step=0.5,
                            value=float(ot.get("horas_reales") or 0), key=f"ot_hrs_{ot['id']}")
                    with cu3:
                        new_accion = st.text_input("Acción tomada",
                            value=str(ot.get("accion_tomada","") or ""), key=f"ot_acc_{ot['id']}")
                    with cu4:
                        new_obs = st.text_input("Observaciones",
                            value=str(ot.get("observaciones","") or ""), key=f"ot_obs_{ot['id']}")
                    if st.button("Guardar", key=f"ot_save_{ot['id']}", type="primary"):
                        run("""UPDATE ordenes_trabajo SET estado=?,horas_reales=?,
                            accion_tomada=?,observaciones=?,updated_at=? WHERE id=?""",
                            (new_est, new_hrs, new_accion, new_obs,
                             datetime.now().strftime("%Y-%m-%d %H:%M"), int(ot["id"])))
                        st.success("Guardado"); st.rerun()

    with tab_nueva:
        if not PUEDE_EDITAR:
            st.warning("Sin permisos para crear OTs")
        else:
            # Buscador de equipo
            busq_eq_ot = st.text_input("Buscar equipo *", placeholder="Tag o nombre...", key="ot_eq_busq")
            eq_ot_df = pd.DataFrame()
            if busq_eq_ot and len(busq_eq_ot) >= 2:
                eq_ot_df = qdf("SELECT tag, spec_nombre_equipo, tipo_codigo FROM equipos WHERE tag LIKE ? OR spec_nombre_equipo LIKE ? LIMIT 20",
                               (f"%{busq_eq_ot}%",f"%{busq_eq_ot}%"))

            tag_ot = ""
            if len(eq_ot_df) > 0:
                tag_ot = st.selectbox("Seleccionar equipo",
                    [f"{r['tag']} — {r.get('spec_nombre_equipo','')}" for _,r in eq_ot_df.iterrows()],
                    label_visibility="collapsed", key="ot_eq_sel")
                tag_ot = tag_ot.split(" — ")[0] if tag_ot else ""

            with st.form("form_ot_nueva"):
                c1,c2 = st.columns(2)
                with c1:
                    ot_titulo  = st.text_input("Título *")
                    ot_tipo    = st.selectbox("Tipo *", ["correctivo","preventivo","inspeccion","lubricacion","calibracion"])
                    ot_prior   = st.selectbox("Prioridad", ["normal","alta","critica","baja"])
                    ot_hest    = st.number_input("Horas estimadas", min_value=0.0, step=0.5, value=2.0)
                with c2:
                    ot_finicio = st.date_input("Fecha inicio", value=date.today())
                    ot_fvenc   = st.date_input("Fecha vencimiento", value=date.today())
                    ot_par     = st.checkbox("Requiere parada")
                    ot_hpar    = st.number_input("Horas de parada", min_value=0.0, step=0.5,
                                                  value=0.0, disabled=not ot_par)
                ot_desc = st.text_area("Descripción *", height=80)
                ot_causa = st.text_area("Causa / Síntoma", height=60)

                if st.form_submit_button("Crear OT", type="primary", use_container_width=True):
                    if ot_titulo and ot_desc and tag_ot:
                        # Generate OT number
                        fecha_str = date.today().strftime("%Y%m%d")
                        try:
                            n_hoy = qdf("SELECT COUNT(*) n FROM ordenes_trabajo WHERE fecha_inicio LIKE ?",
                                        (f"{date.today().isoformat()}%",))
                            seq = int(n_hoy.iloc[0]["n"]) + 1
                        except: seq = 1
                        num_ot = f"OT-{fecha_str}-{seq:03d}"
                        run("""INSERT INTO ordenes_trabajo
                            (numero_ot,tag_equipo,titulo,descripcion,causa_falla,tipo_tarea,
                             prioridad,estado,horas_estimadas,fecha_inicio,fecha_vencimiento,
                             requiere_parada,duracion_parada_h,creado_por,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (num_ot, tag_ot, ot_titulo, ot_desc, ot_causa, ot_tipo, ot_prior,
                             "pendiente", ot_hest, ot_finicio.isoformat(),
                             ot_fvenc.isoformat(), int(ot_par),
                             ot_hpar if ot_par else 0, UID,
                             datetime.now().strftime("%Y-%m-%d %H:%M"),
                             datetime.now().strftime("%Y-%m-%d %H:%M")))
                        st.success(f"OT creada: {num_ot}")
                        st.rerun()
                    else:
                        st.error("Completá tag, título y descripción")


# ══════════════════════════════════════════════════════════════════════════════
# MEDICIONES
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "mediciones":
    st.markdown(f"## {t('mediciones')}")
    tab_mec, tab_elec = st.tabs([t("mecanicas"), t("electricas")])

    # ── MECÁNICAS (VIBRACIONES) ───────────────────────────────────────────────
    with tab_mec:
        st.markdown(f"### {t('vib')}")
        st.caption("ℹ️ mm/s: milímetros por segundo. Límite normal <4.5 mm/s según ISO 10816")

        # Upload
        with st.expander(f"⬆ {t('actualizar')} — Vibraciones"):
            vib_file = st.file_uploader(" ", type=["xlsx"], key="uploader_vib", label_visibility="collapsed")
            if vib_file is not None:
                import io, openpyxl as opx
                try:
                    wb = opx.load_workbook(io.BytesIO(vib_file.read()), data_only=True)
                    ws = wb['Sheet1']
                    existing = set(r[0] for r in qdf("SELECT DISTINCT fecha FROM vibraciones")["fecha"].tolist())
                    col_fechas = {}
                    mes = 4; prev = None
                    for col in range(7, ws.max_column+1):
                        v = ws.cell(4,col).value
                        if v is None: break
                        try:
                            dia = int(float(str(v)))
                            if prev is not None and dia < prev: mes += 1
                            f = date(2026, mes, dia)
                            if f.isoformat() not in existing:
                                col_fechas[col] = f.isoformat()
                            prev = dia
                        except: pass
                        prev = int(float(str(v))) if v else prev

                    TIPOS = ['Bearing Vib.','Agitator 1 Vib','Agitator 2 Vib','Bearing Temp.']
                    area=tag=nombre=""
                    nuevos = 0
                    for row in range(5, ws.max_row+1):
                        v0=ws.cell(row,1).value; v1=ws.cell(row,2).value
                        v2=ws.cell(row,3).value; v3=ws.cell(row,4).value
                        if v0 and str(v0).strip() not in ('None',''): area=str(v0).strip()
                        if v1 and str(v1).strip() not in ('None',''): nombre=str(v1).strip()
                        if v3 and str(v3).strip():
                            raw=str(v3).strip()
                            m2=re.match(r'^(\d{4})([A-Z]{2,3})(\d+.*)$',raw)
                            tag=f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}" if m2 else raw
                        if not v2: continue
                        tipo=str(v2).strip()
                        if tipo not in TIPOS: continue
                        try: lim=float(str(ws.cell(row,5).value or '4.5').replace('<','').replace('>',''))
                        except: lim=4.5
                        unid=str(ws.cell(row,6).value or '').strip()
                        for col,fecha in col_fechas.items():
                            val=ws.cell(row,col).value
                            if val is None: continue
                            val_s=str(val).strip().replace('\u3000','').replace(',','.')
                            if val_s in ('','None','Normal','Stand By','-','N/A','0'): continue
                            try:
                                val_n=float(val_s)
                                if val_n<=0: continue
                            except: continue
                            est='critico' if val_n>lim else 'alerta' if val_n>lim*0.8 else 'normal'
                            run("INSERT INTO vibraciones (tag,equipo_desc,tipo_medicion,valor,unidad,limite,semana,fecha,estado,area) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (tag,nombre,tipo,val_n,unid,lim,0,fecha,est,area))
                            nuevos+=1
                    st.success(f"{t('ok_update')} — {nuevos} mediciones nuevas"); st.rerun()
                except Exception as e: st.error(f"Error: {e}")

        # Datos
        try:
            vib_df = qdf("SELECT tag,equipo_desc,tipo_medicion,valor,unidad,limite,fecha,estado,area FROM vibraciones ORDER BY tag,fecha")
            vib_df["fecha"] = pd.to_datetime(vib_df["fecha"], errors="coerce")

            if len(vib_df) > 0:
                c1,c2,c3,c4 = st.columns(4)
                with c1: st.metric("Equipos", vib_df["tag"].nunique())
                with c2: st.metric("Mediciones", len(vib_df))
                with c3: st.metric("🔴 Críticos", vib_df[vib_df.estado=="critico"]["tag"].nunique())
                with c4:
                    mn = vib_df["fecha"].min(); mx = vib_df["fecha"].max()
                    if pd.notna(mn): st.metric("Período", f"{mn.strftime('%d/%m')} → {mx.strftime('%d/%m/%y')}")

                st.markdown("---")
                c1,c2,c3,c4 = st.columns([2,2,1,1])
                with c1: busq_vib = st.text_input(" ", placeholder=t("buscar"), label_visibility="collapsed", key="vib_busq")
                with c2:
                    tags_vib = ["Todos"] + sorted(vib_df["tag"].unique().tolist())
                    tag_vib = st.selectbox(" ", tags_vib, label_visibility="collapsed", key="vib_tag")
                with c3:
                    tipos_vib = ["Todos"] + sorted(vib_df["tipo_medicion"].unique().tolist())
                    tipo_vib = st.selectbox(" ", tipos_vib, label_visibility="collapsed", key="vib_tipo")
                with c4:
                    est_vib = st.selectbox(" ", ["Todos","critico","alerta","normal"], label_visibility="collapsed", key="vib_est")

                res = vib_df.copy()
                if busq_vib: res = res[res.tag.str.lower().str.contains(busq_vib.lower(),na=False)|res.equipo_desc.fillna("").str.lower().str.contains(busq_vib.lower())]
                if tag_vib != "Todos": res = res[res.tag==tag_vib]
                if tipo_vib != "Todos": res = res[res.tipo_medicion==tipo_vib]
                if est_vib != "Todos": res = res[res.estado==est_vib]

                if tag_vib != "Todos":
                    df_v = res[res.tipo_medicion.str.contains('Vib',na=False)].copy()
                    if len(df_v) > 0:
                        fig = px.line(df_v.sort_values("fecha"), x="fecha", y="valor",
                            color="tipo_medicion", markers=True, height=260,
                            color_discrete_sequence=["#6366f1","#0ea5e9","#14b8a6"],
                            labels={"fecha":"Fecha","valor":"mm/s","tipo_medicion":"Punto"})
                        if df_v["limite"].notna().any():
                            lv = float(df_v["limite"].dropna().iloc[0])
                            fig.add_hline(y=lv,line_dash="dash",line_color="#ef4444",
                                annotation_text=f"Límite {lv} mm/s",annotation_font_color="#ef4444")
                        fig.update_layout(margin=dict(l=0,r=0,t=0,b=0),
                            plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(family="Inter",size=11,color="#888"),
                            xaxis=dict(color="#444",gridcolor="#1e1e22"),
                            yaxis=dict(color="#444",gridcolor="#1e1e22"),
                            legend=dict(font=dict(color="#888"),bgcolor="rgba(0,0,0,0)"))
                        st.plotly_chart(fig, use_container_width=True)
                    if st.button(f"Ver ficha de {tag_vib}", type="primary"):
                        st.session_state.pagina="equipos"
                        st.session_state.eq_busq=tag_vib
                        st.rerun()
                else:
                    # Bar chart últimos valores
                    df_ult = vib_df[vib_df.tipo_medicion.str.contains('Vib',na=False)].copy()
                    if len(df_ult) > 0:
                        ultimo = df_ult.loc[df_ult.groupby("tag")["fecha"].idxmax()].copy()
                        ultimo = ultimo.sort_values("valor",ascending=False).head(20)
                        ultimo["label"] = ultimo["tag"]+" — "+ultimo["equipo_desc"].str[:15]
                        fig2 = px.bar(ultimo,x="label",y="valor",color="estado",height=280,
                            color_discrete_map={"critico":"#ef4444","alerta":"#f59e0b","normal":"#22c55e"},
                            labels={"label":"","valor":"mm/s"})
                        fig2.add_hline(y=4.5,line_dash="dash",line_color="#ef4444",annotation_text="4.5 mm/s")
                        fig2.update_layout(margin=dict(l=0,r=0,t=0,b=80),
                            plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(family="Inter",size=10,color="#888"),
                            xaxis=dict(color="#444",tickangle=45,tickfont=dict(size=8)),
                            yaxis=dict(color="#444",gridcolor="#1e1e22"),
                            legend=dict(font=dict(color="#888"),bgcolor="rgba(0,0,0,0)",orientation="h",y=1.1))
                        st.plotly_chart(fig2, use_container_width=True)

                show = res[["tag","equipo_desc","tipo_medicion","valor","unidad","limite","fecha","estado"]].copy()
                show["fecha"] = pd.to_datetime(show["fecha"],errors="coerce").dt.strftime("%d/%m/%Y")
                st.dataframe(show.rename(columns={"tag":"Tag","equipo_desc":"Equipo","tipo_medicion":"Medición",
                    "valor":"Valor","unidad":"Ud","limite":"Límite","fecha":"Fecha","estado":"Estado"}),
                    use_container_width=True, hide_index=True, height=280)

                # Registro manual
                with st.expander("+ Registrar medición manual"):
                    with st.form("form_vib_manual"):
                        c1,c2,c3 = st.columns(3)
                        with c1:
                            eq_all = qdf("SELECT tag FROM equipos ORDER BY tag")
                            new_tag = st.selectbox("Equipo *", eq_all["tag"].tolist())
                            new_tipo = st.selectbox("Tipo", ["Bearing Vib.","Agitator 1 Vib","Agitator 2 Vib","Bearing Temp."])
                        with c2:
                            new_val = st.number_input("Valor *", min_value=0.0, step=0.1)
                            new_unit = st.selectbox("Unidad", ["mm/s","℃"])
                            new_lim = st.number_input("Límite", min_value=0.0, value=4.5, step=0.1)
                        with c3:
                            new_fecha = st.date_input("Fecha", value=date.today())
                            new_area = st.text_input("Área")
                        if st.form_submit_button("Registrar", type="primary"):
                            est_n = 'critico' if new_val>new_lim else 'alerta' if new_val>new_lim*0.8 else 'normal'
                            run("INSERT INTO vibraciones (tag,equipo_desc,tipo_medicion,valor,unidad,limite,semana,fecha,estado,area) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (new_tag,new_tag,new_tipo,new_val,new_unit,new_lim,0,new_fecha.isoformat(),est_n,new_area))
                            st.success("Registrado"); st.rerun()
        except Exception as e:
            st.error(f"Error vibraciones: {e}")

    # ── ELÉCTRICAS ────────────────────────────────────────────────────────────
    with tab_elec:
        st.markdown(f"### {t('electricas')}")
        sub_ht, sub_plc, sub_sem = st.tabs([t("ht"), t("plc"), t("semanal")])

        # HEAT TRACE
        with sub_ht:
            with st.expander(f"⬆ {t('actualizar')} — Heat Trace"):
                ht_file = st.file_uploader(" ", type=["xlsx"], key="uploader_ht", label_visibility="collapsed")
                if ht_file is not None:
                    import io, openpyxl as opx2
                    try:
                        wb2 = opx2.load_workbook(io.BytesIO(ht_file.read()), data_only=True)
                        PANELES = {1:("0000-EHT-001","Liming Plant"),4:("0000-EHT-002","LC Plant"),
                            7:("0000-EHT-003","LC Plant"),10:("0000-EHT-004","Water Plant"),
                            13:("0000-EHT-005","Truck Shop"),16:("0000-EHT-006","6700 Plant")}
                        existing_ht = set(qdf("SELECT DISTINCT fecha FROM mediciones_heat_trace")["fecha"].tolist())
                        nuevos_ht = 0
                        for sh in wb2.sheetnames:
                            if sh == 'Data 위치': continue
                            try: p=sh.strip().split('.'); fecha2=f"20{p[0]}-{p[1]}-{p[2]}"
                            except: continue
                            if fecha2 in existing_ht: continue
                            ws2=wb2[sh]
                            for ci,(panel,ubic) in PANELES.items():
                                volt_f=None; corr_f=None
                                try: volt_f=float(ws2.cell(4,ci+1).value)
                                except: pass
                                try: corr_f=float(ws2.cell(5,ci+1).value)
                                except: pass
                                mc=str(ws2.cell(6,ci+1).value or '')
                                hora=str(ws2.cell(7,ci+1).value or '')
                                for row in range(10,40):
                                    cct_l=ws2.cell(row,ci).value
                                    cct_v=ws2.cell(row,ci+1).value
                                    if not cct_l: continue
                                    amp_f=None
                                    try: amp_f=float(cct_v)
                                    except: pass
                                    run("INSERT INTO mediciones_heat_trace (fecha,panel,ubicacion,voltaje_v,corriente_a,mc_estado,hora_insp,cct,amperaje_a) VALUES (?,?,?,?,?,?,?,?,?)",
                                        (fecha2,panel,ubic,volt_f,corr_f,mc,hora,str(cct_l).strip(),amp_f))
                                    nuevos_ht+=1
                        st.success(f"{t('ok_update')} — {nuevos_ht} registros nuevos"); st.rerun()
                    except Exception as e: st.error(f"Error: {e}")

            try:
                ht_df = qdf("SELECT fecha,panel,ubicacion,voltaje_v,cct,amperaje_a FROM mediciones_heat_trace ORDER BY fecha,panel,cct")
                if len(ht_df)==0:
                    st.info(t("sin_datos"))
                else:
                    c1,c2 = st.columns(2)
                    with c1: panel_sel = st.selectbox("Panel", sorted(ht_df["panel"].unique().tolist()), key="ht_panel")
                    with c2:
                        fechas_ht = ["Última"]+sorted(ht_df["fecha"].unique().tolist(),reverse=True)
                        fecha_ht = st.selectbox("Fecha", fechas_ht, key="ht_fecha")

                    df_p = ht_df[ht_df.panel==panel_sel]
                    fu = df_p["fecha"].max() if fecha_ht=="Última" else fecha_ht
                    df_day = df_p[df_p.fecha==fu]
                    tipo_panel = "Monofásico" if panel_sel=="0000-EHT-005" else "Trifásico"

                    if len(df_day) > 0:
                        row_p = df_day.iloc[0]
                        import numpy as np
                        amps = df_day.dropna(subset=["amperaje_a"])["amperaje_a"].values
                        if len(amps) > 0 and tipo_panel=="Trifásico":
                            desb = (np.max(amps)-np.min(amps))/np.min(amps)*100 if np.min(amps)>0 else 0
                        else: desb = 0
                        color_d = "#4ade80" if desb<10 else "#f59e0b" if desb<20 else "#ef4444"
                        c1,c2,c3 = st.columns(3)
                        with c1: st.metric(f"Panel ({tipo_panel})", panel_sel)
                        with c2: st.metric("Voltaje", f"{row_p.get('voltaje_v','-')} V")
                        with c3: st.metric("Desbalance CCTs", f"{desb:.1f}%")

                    # CCT selector - sorted numerically
                    def cct_key(c):
                        m2 = re.search(r"\d+", str(c))
                        return int(m2.group()) if m2 else 999
                    ccts = sorted(df_day["cct"].dropna().unique().tolist(), key=cct_key)
                    cct_sel = st.selectbox("CCT", ccts, key="ht_cct") if ccts else None

                    if cct_sel:
                        df_cct = df_p[df_p.cct==cct_sel].dropna(subset=["amperaje_a"]).copy()
                        df_cct["fecha"] = pd.to_datetime(df_cct["fecha"])
                        if len(df_cct) > 0:
                            fig_ht = px.line(df_cct.sort_values("fecha"),x="fecha",y="amperaje_a",
                                markers=True,height=240,color_discrete_sequence=["#6366f1"],
                                labels={"fecha":"","amperaje_a":"A"})
                            fig_ht.update_layout(title=dict(text=f"{panel_sel} — {cct_sel}",font=dict(color="#aaa",size=12),x=0.5),
                                margin=dict(l=0,r=0,t=30,b=0),
                                plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)",
                                font=dict(family="Inter",size=11,color="#888"),
                                xaxis=dict(color="#444",gridcolor="#1e1e22"),
                                yaxis=dict(color="#444",gridcolor="#1e1e22",title="A"))
                            st.plotly_chart(fig_ht,use_container_width=True)

                            vals_ht = df_cct["amperaje_a"].dropna()
                            c1,c2,c3,c4 = st.columns(4)
                            with c1: st.metric("Media",f"{vals_ht.mean():.2f} A")
                            with c2: st.metric("Mediana",f"{vals_ht.median():.2f} A")
                            with c3: st.metric("Varianza",f"{vals_ht.var():.3f}")
                            with c4: st.metric("Rango",f"{vals_ht.min():.1f}–{vals_ht.max():.1f} A")

                    st.markdown("---")
                    st.markdown(f"#### Todos los CCTs — {panel_sel} — {fu}")
                    day_show = df_day[["cct","amperaje_a"]].copy().dropna(subset=["cct"])
                    day_show["_s"] = day_show["cct"].apply(lambda c: cct_key(str(c)))
                    day_show = day_show.sort_values("_s").drop("_s",axis=1)
                    day_show["amperaje_a"] = day_show["amperaje_a"].apply(lambda x: f"{x:.2f} A" if pd.notna(x) else "—")
                    st.dataframe(day_show.rename(columns={"cct":"CCT","amperaje_a":"Amperaje"}),use_container_width=True,hide_index=True)
            except Exception as e: st.error(f"Error Heat Trace: {e}")

        # PLC
        with sub_plc:
            with st.expander(f"⬆ {t('actualizar')} — PLC"):
                plc_file = st.file_uploader(" ", type=["xlsx"], key="uploader_plc", label_visibility="collapsed")
                if plc_file is not None:
                    import io, openpyxl as opx3
                    try:
                        wb3 = opx3.load_workbook(io.BytesIO(plc_file.read()), data_only=True)
                        ANOM = {'MAINT','BAF','BATT1F','BATT2F'}
                        fechas_plc = set(qdf("SELECT DISTINCT fecha FROM mediciones_plc")["fecha"].tolist())
                        BLOQUES = [
                            (7,24,[("2100-PCS7-001 (A)",1,2),("2100-PCS7-001 (A)",3,4),
                                   ("2100-PCS7-001 (B)",6,7),("2100-PCS7-001 (B)",8,9),
                                   ("3000-PCS7-001 (A)",11,12),("3000-PCS7-001 (A)",13,14),
                                   ("3000-PCS7-001 (B)",16,17),("3000-PCS7-001 (B)",18,19)]),
                            (30,44,[("4000-PCS7-001 (A)",1,2),("4000-PCS7-001 (A)",3,4),
                                    ("4000-PCS7-001 (B)",6,7),("4000-PCS7-001 (B)",8,9),
                                    ("6000-PCS7-001 (A)",11,12),("6000-PCS7-001 (A)",13,14),
                                    ("6000-PCS7-001 (B)",16,17),("6000-PCS7-001 (B)",18,19)])]
                        nuevos_plc = 0
                        for sh in wb3.sheetnames:
                            if sh=='Data 위치': continue
                            try: p=sh.strip().split('.'); fecha2=f"20{p[0]}-{p[1]}-{p[2]}"
                            except: continue
                            if fecha2 in fechas_plc: continue
                            ws3=wb3[sh]
                            mods={}
                            for rh in [4,27]:
                                for c in range(1,20):
                                    v=ws3.cell(rh,c).value
                                    if v and str(v).strip() in ('Power Module','CPU Module'):
                                        mods[c]=str(v).strip()
                            for fi,ff,plcs3 in BLOQUES:
                                for pid,cn,ce in plcs3:
                                    mod=mods.get(cn,'Unknown')
                                    for row in range(fi,ff+1):
                                        lamp=ws3.cell(row,cn).value
                                        est=ws3.cell(row,ce).value
                                        if not lamp or str(lamp).strip() in ('Name','Lamp','None',''): continue
                                        ls=str(lamp).strip(); es=str(est).strip() if est else ''
                                        anom=1 if (ls in ANOM and es in ('O','ON','on','On','o')) else 0
                                        run("INSERT INTO mediciones_plc (fecha,plc_id,modulo,lampara,estado,es_anomalia) VALUES (?,?,?,?,?,?)",
                                            (fecha2,pid,mod,ls,es,anom))
                                        nuevos_plc+=1
                        st.success(f"{t('ok_update')} — {nuevos_plc} registros"); st.rerun()
                    except Exception as e: st.error(f"Error: {e}")

            try:
                plc_df = qdf("SELECT fecha,plc_id,modulo,lampara,estado,es_anomalia FROM mediciones_plc ORDER BY fecha DESC,plc_id")
                if len(plc_df)==0:
                    st.info(t("sin_datos"))
                else:
                    c1,c2,c3,c4 = st.columns(4)
                    with c1: st.metric("Días registrados",plc_df["fecha"].nunique())
                    with c2: st.metric("PLCs",plc_df["plc_id"].nunique())
                    with c3: st.metric("🔴 Anomalías totales",len(plc_df[plc_df.es_anomalia==1]))
                    with c4: st.metric("Días con anomalía",plc_df[plc_df.es_anomalia==1]["fecha"].nunique())

                    with st.expander("ℹ️ Anomalías: MAINT, BAF, BATT1F, BATT2F"):
                        st.caption("**BAF:** Battery Alarm Fault — falla en batería de respaldo del PLC")
                        st.caption("**BATT1F/BATT2F:** falla en batería 1 o 2 del módulo CPU")
                        st.caption("**MAINT:** modo mantenimiento activo")

                    st.markdown("---")
                    st.markdown("#### Estado actual de PLCs")
                    ultima = plc_df["fecha"].max()
                    plcs_all = sorted(plc_df["plc_id"].unique().tolist())
                    ec = st.columns(min(len(plcs_all),4))
                    for i,pid in enumerate(plcs_all):
                        with ec[i%4]:
                            anom_p = plc_df[(plc_df.plc_id==pid)&(plc_df.es_anomalia==1)]
                            anom_hoy = anom_p[anom_p.fecha==ultima]
                            color = "#ef4444" if len(anom_hoy)>0 else "#22c55e"
                            estado_txt = "⚠️ ANOMALÍA" if len(anom_hoy)>0 else "✅ OK"
                            lamps_txt = ", ".join(anom_hoy["lampara"].unique().tolist()) if len(anom_hoy)>0 else ""
                            det = f"<br><span style='font-size:10px;color:#fca5a5'>{lamps_txt}</span>" if lamps_txt else ""
                            dias_n = int(anom_p["fecha"].nunique())
                            dias_txt = f"{dias_n} " + ("día" if dias_n==1 else "días") + " con anomalía"
                            html_card = (
                                f'<div style="background:#111113;border:1.5px solid {color};border-radius:8px;'
                                f'padding:10px 14px;margin-bottom:8px;text-align:center">'
                                f'<div style="font-family:monospace;font-size:10px;color:#888">{pid}</div>'
                                f'<div style="font-size:13px;font-weight:600;color:{color};margin-top:3px">{estado_txt}</div>'
                                f'{det}'
                                f'<div style="font-size:10px;color:#444;margin-top:3px">{dias_txt}</div>'
                                f'</div>')
                            st.markdown(html_card, unsafe_allow_html=True)

                    st.markdown("---")
                    c1,c2,c3 = st.columns(3)
                    with c1: plc_sel2 = st.selectbox("PLC",["Todos"]+plcs_all,key="plc_sel2",label_visibility="collapsed")
                    with c2: lamp_sel2 = st.selectbox("Lámpara",["Todas"]+sorted(plc_df["lampara"].unique().tolist()),key="lamp_sel2",label_visibility="collapsed")
                    with c3: solo_anom2 = st.checkbox("Solo anomalías",value=True,key="solo_anom2")

                    res_plc = plc_df.copy()
                    if plc_sel2!="Todos": res_plc=res_plc[res_plc.plc_id==plc_sel2]
                    if lamp_sel2!="Todas": res_plc=res_plc[res_plc.lampara==lamp_sel2]
                    if solo_anom2: res_plc=res_plc[res_plc.es_anomalia==1]

                    if len(res_plc)==0:
                        st.success("✅ Sin anomalías con los filtros seleccionados")
                    else:
                        anom_day = res_plc.groupby(["fecha","plc_id"]).size().reset_index(name="n")
                        fig_plc = px.bar(anom_day,x="fecha",y="n",color="plc_id",height=220,barmode="stack",
                            color_discrete_sequence=["#6366f1","#0ea5e9","#14b8a6","#8b5cf6","#ef4444","#f59e0b","#22c55e","#64748b"],
                            labels={"fecha":"","n":"Anomalías","plc_id":"PLC"})
                        fig_plc.update_layout(margin=dict(l=0,r=0,t=0,b=0),
                            plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(family="Inter",size=10,color="#888"),
                            xaxis=dict(color="#444",tickangle=45,tickfont=dict(size=8)),
                            yaxis=dict(color="#444",gridcolor="#1e1e22"),
                            legend=dict(font=dict(color="#888"),bgcolor="rgba(0,0,0,0)"))
                        st.plotly_chart(fig_plc,use_container_width=True)
                        st.dataframe(res_plc.rename(columns={"fecha":"Fecha","plc_id":"PLC","modulo":"Módulo","lampara":"Lámpara","estado":"Estado","es_anomalia":"Anom."}),
                            use_container_width=True,hide_index=True,height=280)
            except Exception as e: st.error(f"Error PLC: {e}")

        # SEMANAL
        with sub_sem:
            with st.expander(f"⬆ {t('actualizar')} — Inspección Semanal"):
                sem_file = st.file_uploader(" ", type=["xlsx"], key="uploader_sem", label_visibility="collapsed")
                if sem_file is not None:
                    import io, openpyxl as opx4
                    try:
                        wb4 = opx4.load_workbook(io.BytesIO(sem_file.read()), data_only=True)
                        fechas_sem = set(qdf("SELECT DISTINCT fecha_hoja FROM mediciones_semanal WHERE fecha_hoja IS NOT NULL")["fecha_hoja"].tolist())
                        nuevos_sem = 0
                        for sh in wb4.sheetnames:
                            if sh in ('Rangos',): continue
                            try: p=sh.strip().split('.'); fh=f"20{p[0]}-{p[1]}-{p[2]}"
                            except: continue
                            if fh in fechas_sem: continue
                            ws4=wb4[sh]
                            for row in range(4,ws4.max_row+1):
                                tag_e=ws4.cell(row,2).value
                                if not tag_e: continue
                                fec_e=ws4.cell(row,4).value
                                fecha_r=fec_e.strftime('%Y-%m-%d') if hasattr(fec_e,'strftime') else fh
                                val_e=ws4.cell(row,6).value
                                val_s=str(val_e).strip() if val_e is not None else None
                                val_n=None
                                if val_e is not None:
                                    try: val_n=float(val_e)
                                    except:
                                        m3=re.search(r'[-+]?\d*\.?\d+',str(val_e))
                                        if m3:
                                            try: val_n=float(m3.group())
                                            except: pass
                                est_e=str(ws4.cell(row,5).value or '').strip().lower()
                                tipo_e=str(ws4.cell(row,9).value or '').strip()
                                tag_s=str(tag_e).strip()
                                grp=re.search(r'-([A-Z]{2,4})-',tag_s)
                                grupo=grp.group(1) if grp else ''
                                run("INSERT INTO mediciones_semanal (fecha,tag,descripcion,estado_alim,valor,valor_num,observacion,tipo,grupo_sensor,tipo_sensor,fecha_hoja) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                    (fecha_r,tag_s,str(ws4.cell(row,3).value or '').strip(),
                                     est_e if est_e else None,val_s,val_n,
                                     str(ws4.cell(row,7).value or '').strip() if ws4.cell(row,7).value else None,
                                     tipo_e,grupo,tipo_e,fh))
                                nuevos_sem+=1
                        st.success(f"{t('ok_update')} — {nuevos_sem} registros"); st.rerun()
                    except Exception as e: st.error(f"Error: {e}")

            try:
                sem_df = qdf("SELECT fecha,tag,descripcion,estado_alim,valor,valor_num,observacion,tipo_sensor,fecha_hoja FROM mediciones_semanal ORDER BY fecha_hoja DESC,tag")
                if len(sem_df)==0:
                    st.info(t("sin_datos"))
                else:
                    fechas_h = sorted(sem_df["fecha_hoja"].dropna().unique().tolist(),reverse=True)
                    con_dato = sem_df["valor"].notna().sum()
                    pct = round(con_dato/max(len(sem_df),1)*100)

                    c1,c2,c3,c4 = st.columns(4)
                    with c1: st.metric("Semanas",len(fechas_h))
                    with c2: st.metric("Instrumentos",sem_df["tag"].nunique())
                    with c3: st.metric("% Realizadas",f"{pct}%")
                    with c4: st.metric("🔴 OFF",len(sem_df[sem_df.estado_alim=="off"]))

                    st.markdown("---")

                    # Completitud por semana
                    st.markdown("#### % Completitud por semana")
                    comp = []
                    for fh in sorted(fechas_h):
                        df_fh = sem_df[sem_df.fecha_hoja==fh]
                        con = df_fh["valor"].notna().sum()
                        tot = len(df_fh)
                        comp.append({"Semana":fh,"Realizadas":con,"Total":tot,"Pct":round(con/max(tot,1)*100)})
                    fig_c = px.bar(pd.DataFrame(comp),x="Semana",y="Pct",height=180,
                        color="Pct",color_continuous_scale=["#ef4444","#f59e0b","#22c55e"],range_color=[0,100],
                        text="Pct",labels={"Semana":"","Pct":"%"},hover_data={"Realizadas":True,"Total":True})
                    fig_c.update_traces(texttemplate="%{text}%",textposition="outside",textfont=dict(color="#aaa",size=9))
                    fig_c.update_layout(margin=dict(l=0,r=0,t=0,b=0),coloraxis_showscale=False,
                        plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="Inter",size=10,color="#888"),
                        xaxis=dict(color="#444",tickangle=45),yaxis=dict(color="#444",gridcolor="#1e1e22",range=[0,115]))
                    st.plotly_chart(fig_c,use_container_width=True)

                    # Estado ON/OFF/AN
                    ultima_s = fechas_h[0]
                    df_ult = sem_df[sem_df.fecha_hoja==ultima_s]
                    on_n=len(df_ult[df_ult.estado_alim=="on"])
                    an_n=len(df_ult[df_ult.estado_alim=="an"])
                    off_n=len(df_ult[df_ult.estado_alim=="off"])
                    sin_n=len(df_ult[~df_ult.estado_alim.isin(["on","an","off"])])

                    c1,c2 = st.columns(2)
                    with c1:
                        pie_d = pd.DataFrame({"Estado":["ON — digital encendido","AN — analógico","OFF — apagado","Sin dato"],"n":[on_n,an_n,off_n,sin_n]})
                        pie_d = pie_d[pie_d.n>0]
                        fig_pie = px.pie(pie_d,values="n",names="Estado",height=200,
                            color_discrete_map={"ON — digital encendido":"#22c55e","AN — analógico":"#3b82f6","OFF — apagado":"#ef4444","Sin dato":"#374151"})
                        fig_pie.update_layout(margin=dict(l=0,r=0,t=10,b=0),
                            plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)",
                            font=dict(family="Inter",size=10,color="#888"),
                            legend=dict(font=dict(color="#888"),bgcolor="rgba(0,0,0,0)"))
                        fig_pie.update_traces(textposition="inside",textinfo="percent",textfont=dict(size=9,color="white"))
                        st.plotly_chart(fig_pie,use_container_width=True)
                        st.caption(f"Inspección: {ultima_s} · ON=digital encendido · AN=analógico · OFF=apagado")
                    with c2:
                        df_off = df_ult[df_ult.estado_alim=="off"][["tag","descripcion","observacion"]].copy()
                        if len(df_off)>0:
                            st.markdown("**Sensores OFF:**")
                            st.dataframe(df_off.rename(columns={"tag":"Tag","descripcion":"Descripción","observacion":"Motivo"}),use_container_width=True,hide_index=True)
                        else:
                            st.success("✅ Sin sensores apagados")

                    st.markdown("---")

                    # Filtros
                    c1,c2,c3,c4 = st.columns(4)
                    with c1: fs_sel = st.selectbox(" ",["Todas"]+fechas_h,label_visibility="collapsed",key="sem_f")
                    with c2:
                        tipos_sem = ["Todos"]+sorted(sem_df["tipo_sensor"].dropna().unique().tolist())
                        ts_sel = st.selectbox(" ",tipos_sem,label_visibility="collapsed",key="sem_ts")
                    with c3:
                        alim_sel = st.selectbox(" ",["Todos","on","an","off"],label_visibility="collapsed",key="sem_alim",
                            format_func=lambda x:{"Todos":"Todos","on":"ON","an":"AN","off":"OFF"}.get(x,x))
                    with c4: busq_sem = st.text_input(" ",placeholder="Buscar por tag (ej: 3100)",label_visibility="collapsed",key="sem_b")

                    res_sem = sem_df.copy()
                    if fs_sel!="Todas": res_sem=res_sem[res_sem.fecha_hoja==fs_sel]
                    if ts_sel!="Todos": res_sem=res_sem[res_sem.tipo_sensor==ts_sel]
                    if alim_sel!="Todos": res_sem=res_sem[res_sem.estado_alim==alim_sel]
                    if busq_sem and busq_sem.strip():
                        b=busq_sem.strip().lower()
                        res_sem=res_sem[res_sem.tag.str.lower().str.contains(b,na=False)|res_sem.descripcion.fillna("").str.lower().str.contains(b)]

                    con_f=res_sem["valor"].notna().sum(); tot_f=len(res_sem)
                    st.caption(f"📊 {con_f}/{tot_f} mediciones con dato ({round(con_f/max(tot_f,1)*100)}%) · ℹ️ ON=digital · AN=analógico · OFF=apagado")

                    # Gráfico tendencia - vinculado al filtro
                    tags_fil = ["— Seleccionar —"]+sorted(res_sem["tag"].unique().tolist())
                    tag_sem_sel = st.selectbox(" ",tags_fil,label_visibility="collapsed",key="sem_tag_sel")

                    if tag_sem_sel != "— Seleccionar —":
                        df_tag = sem_df[sem_df.tag==tag_sem_sel].dropna(subset=["valor_num"]).copy()
                        df_tag["fp"] = pd.to_datetime(df_tag["fecha_hoja"])
                        df_tag = df_tag.sort_values("fp")
                        if len(df_tag) > 1:
                            vals2 = df_tag["valor_num"]
                            media2 = vals2.mean(); desv2 = vals2.std()
                            def cls2(v):
                                d=abs(v-media2)
                                return "🔴 >2σ" if d>2*desv2 else "🟠 >1σ" if d>desv2 else "🟢 Normal"
                            df_tag["cls"]=df_tag["valor_num"].apply(cls2)
                            fig_s=go.Figure()
                            fig_s.add_trace(go.Scatter(x=df_tag["fp"],y=df_tag["valor_num"],mode="lines",line=dict(color="#0ea5e9",width=1.5),showlegend=False))
                            for cls,clr in [("🟢 Normal","#22c55e"),("🟠 >1σ","#f59e0b"),("🔴 >2σ","#ef4444")]:
                                mk=df_tag["cls"]==cls
                                if mk.any():
                                    fig_s.add_trace(go.Scatter(x=df_tag.loc[mk,"fp"],y=df_tag.loc[mk,"valor_num"],
                                        mode="markers",marker=dict(color=clr,size=8,line=dict(color="white",width=1)),name=cls))
                            fr=[df_tag["fp"].min(),df_tag["fp"].max()]
                            fig_s.add_trace(go.Scatter(x=fr,y=[media2,media2],mode="lines",line=dict(color="#60a5fa",width=1,dash="dash"),name=f"Media: {media2:.3f}"))
                            fig_s.add_trace(go.Scatter(x=fr,y=[media2+desv2,media2+desv2],mode="lines",line=dict(color="#f59e0b",width=1,dash="dot"),name=f"+1σ"))
                            fig_s.add_trace(go.Scatter(x=fr,y=[media2-desv2,media2-desv2],mode="lines",line=dict(color="#f59e0b",width=1,dash="dot"),showlegend=False))
                            fig_s.add_trace(go.Scatter(x=fr,y=[media2+2*desv2,media2+2*desv2],mode="lines",line=dict(color="#ef4444",width=1,dash="dot"),name=f"+2σ"))
                            fig_s.add_trace(go.Scatter(x=fr,y=[media2-2*desv2,media2-2*desv2],mode="lines",line=dict(color="#ef4444",width=1,dash="dot"),showlegend=False))
                            fig_s.update_layout(title=dict(text=f"{tag_sem_sel} — {str(df_tag['descripcion'].iloc[0])[:50]}",font=dict(color="#aaa",size=11),x=0.5),
                                height=260,margin=dict(l=0,r=0,t=30,b=0),
                                plot_bgcolor="rgba(0,0,0,0)",paper_bgcolor="rgba(0,0,0,0)",
                                font=dict(family="Inter",size=10,color="#888"),
                                xaxis=dict(color="#444",gridcolor="#1e1e22"),
                                yaxis=dict(color="#444",gridcolor="#1e1e22"),
                                legend=dict(font=dict(color="#888",size=9),bgcolor="rgba(0,0,0,0)",orientation="h",y=-0.3,x=0))
                            st.plotly_chart(fig_s,use_container_width=True)
                            c1,c2,c3,c4=st.columns(4)
                            with c1: st.metric("Media",f"{media2:.4f}")
                            with c2: st.metric("Mediana",f"{vals2.median():.4f}")
                            with c3: st.metric("Varianza",f"{vals2.var():.6f}")
                            with c4: st.metric("Desvío σ",f"{desv2:.4f}")
                        else:
                            st.info("Sin suficientes datos para graficar")

                    st.dataframe(res_sem[["tag","descripcion","fecha_hoja","tipo_sensor","estado_alim","valor","observacion"]].rename(columns={
                        "tag":"Tag","descripcion":"Descripción","fecha_hoja":"Semana","tipo_sensor":"Tipo",
                        "estado_alim":"Alim.","valor":"Valor","observacion":"Observación"}),
                        use_container_width=True,hide_index=True,height=320)
            except Exception as e: st.error(f"Error semanal: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# PURCHASE REQUESTS
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "pr":
    st.markdown("## Seguimiento de Pedidos")

    # Upload Excel
    with st.expander(f"⬆ {t('actualizar')} — Purchase Requests"):
        pr_file = st.file_uploader(" ", type=["xlsx"], key="uploader_pr", label_visibility="collapsed")
        if pr_file is not None:
            import io, openpyxl as opx5
            try:
                wb5 = opx5.load_workbook(io.BytesIO(pr_file.read()), data_only=True)
                ws5 = wb5.active
                def _fmtd(v):
                    if v is None: return ''
                    if hasattr(v,'strftime'): return v.strftime('%Y-%m-%d')
                    s=str(v).strip()
                    if '/' in s:
                        try: p=s.split('/'); return f"{p[2]}-{p[1].zfill(2)}-{p[0].zfill(2)}"
                        except: pass
                    return '' if s in ('None','nan','','-') else s[:10]
                def _cln(v):
                    if v is None: return ''
                    s=str(v).strip(); return '' if s in ('None','nan','','-') else s
                run("DELETE FROM purchase_requests")
                est_map={'pending':'Pending','in progress':'In progress','in transit':'In transit',
                         'delivered':'Delivered','cancelled':'Cancelled'}
                cnt=0
                for row in range(9, ws5.max_row+1):
                    sol=_cln(ws5.cell(row,1).value)
                    if not sol: continue
                    tit=_cln(ws5.cell(row,3).value)
                    if not tit: continue
                    try: q_n=int(float(str(ws5.cell(row,6).value or ws5.cell(row,5).value or 0)))
                    except: q_n=0
                    est=_cln(ws5.cell(row,18).value)
                    est_n=est_map.get(est.lower().strip(),est) if est else 'In progress'
                    run("INSERT INTO purchase_requests (solicitante,solicitud,titulo,q_codes_n,fecha_solicitud,fecha_aprobacion,fecha_pr,numero_pr,comprador,estado,ultima_consulta,observaciones) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sol,_cln(ws5.cell(row,2).value),tit,q_n,
                         _fmtd(ws5.cell(row,7).value),_fmtd(ws5.cell(row,8).value),
                         _fmtd(ws5.cell(row,10).value),_cln(ws5.cell(row,11).value),
                         _cln(ws5.cell(row,14).value),est_n,
                         _fmtd(ws5.cell(row,20).value),_cln(ws5.cell(row,21).value)))
                    cnt+=1
                st.success(f"{t('ok_update')} — {cnt} PRs"); st.rerun()
            except Exception as e: st.error(f"Error: {e}")

    try:
        pr_df = qdf("SELECT * FROM purchase_requests ORDER BY id")
    except:
        pr_df = pd.DataFrame()

    if len(pr_df) > 0:
        c1,c2,c3,c4,c5 = st.columns(5)
        with c1: st.metric("Total",len(pr_df))
        with c2: st.metric("🔵 In progress",len(pr_df[pr_df.estado=="In progress"]))
        with c3: st.metric("🟡 Pending",len(pr_df[pr_df.estado=="Pending"]))
        with c4: st.metric("🟠 In transit",len(pr_df[pr_df.estado=="In transit"]))
        with c5: st.metric("✅ Delivered",len(pr_df[pr_df.estado=="Delivered"]))
        st.markdown("---")

        # Filtro
        c1,c2,c3 = st.columns([3,2,2])
        with c1: busq_pr = st.text_input(" ",placeholder="Filtrar...",label_visibility="collapsed",key="pr_busq")
        with c2:
            est_pr_opts = ["Todos","In progress","Pending","In transit","Delivered","Cancelled"]
            est_pr = st.selectbox(" ",est_pr_opts,label_visibility="collapsed",key="pr_est_f")
        with c3:
            sol_opts = ["Todos"]+sorted(pr_df.solicitante.dropna().unique().tolist())
            sol_pr = st.selectbox(" ",sol_opts,label_visibility="collapsed",key="pr_sol_f")

        res_pr = pr_df.copy()
        if busq_pr: res_pr=res_pr[res_pr.titulo.fillna("").str.lower().str.contains(busq_pr.lower())|res_pr.solicitante.fillna("").str.lower().str.contains(busq_pr.lower())|res_pr.solicitud.fillna("").str.lower().str.contains(busq_pr.lower())]
        if est_pr!="Todos": res_pr=res_pr[res_pr.estado==est_pr]
        if sol_pr!="Todos": res_pr=res_pr[res_pr.solicitante==sol_pr]

        st.caption(f"{len(res_pr)} pedidos")
        with st.expander("ℹ️ Glosario"):
            st.caption("**Q code:** identificador único en PosAppia para crear una PR")
            st.caption("**PR:** Purchase Request — solicitud formal en POSPIA/GoWorks")
            st.caption("**GoWorks:** sistema de aprobación de compras previo a POSPIA")

        # Tabla HTML
        def est_badge(e):
            css={"In progress":"background:#1a2744;color:#60a5fa","Pending":"background:#2a1f0a;color:#fbbf24",
                 "In transit":"background:#1a1506;color:#f97316","Delivered":"background:#0f2a1e;color:#4ade80",
                 "Cancelled":"background:#1a1a1e;color:#555"}
            return f'<span style="{css.get(e,"background:#111;color:#666")};padding:2px 6px;border-radius:3px;font-size:10px;white-space:nowrap">{e}</span>'

        def fd(v): return str(v)[:10] if v and str(v).strip() not in ('','None','nan') else '—'

        filas = ""
        for _,pr in res_pr.iterrows():
            filas += (f"<tr><td style='white-space:nowrap'>{pr.get('solicitante','')}</td>"
                f"<td style='color:#888'>{pr.get('solicitud','')}</td>"
                f"<td style='min-width:160px'>{pr.get('titulo','')}</td>"
                f"<td style='text-align:center'>{pr.get('q_codes_n',0)}</td>"
                f"<td style='color:#555'>{fd(pr.get('fecha_solicitud'))}</td>"
                f"<td style='color:#555'>{fd(pr.get('fecha_aprobacion'))}</td>"
                f"<td style='color:#555'>{fd(pr.get('fecha_pr'))}</td>"
                f"<td style='color:#60a5fa;font-weight:500'>{pr.get('numero_pr','') or '—'}</td>"
                f"<td>{pr.get('comprador','') or '—'}</td>"
                f"<td>{est_badge(pr.get('estado',''))}</td>"
                f"<td style='color:#555'>{fd(pr.get('ultima_consulta'))}</td>"
                f"<td style='color:#666;font-size:10px;min-width:160px'>{pr.get('observaciones','') or ''}</td></tr>")

        import streamlit.components.v1 as components
        tabla_html = f"""<!DOCTYPE html><html><head><style>
        body{{margin:0;padding:0;background:transparent;font-family:Inter,Arial,sans-serif}}
        table{{width:100%;border-collapse:collapse;font-size:11px}}
        th{{background:#1a1a1e;color:#888;padding:7px 9px;border:1px solid #222;text-align:left;font-weight:500;white-space:nowrap}}
        td{{padding:5px 9px;border:1px solid #1e1e22;color:#ccc;vertical-align:top}}
        tr:nth-child(even) td{{background:#111113}}
        tr:nth-child(odd) td{{background:#0f0f10}}
        tr:hover td{{background:#161618}}
        </style></head><body><div style="overflow-x:auto">
        <table><thead><tr>
        <th>Solicitante</th><th>N° Solicitud</th><th>Título</th><th>Q codes</th>
        <th>F. Solicitud</th><th>F. Aprobación</th><th>F. PR</th><th>N° PR</th>
        <th>Comprador</th><th>Estado</th><th>Última consulta</th><th>Observaciones</th>
        </tr></thead><tbody>{filas}</tbody></table></div></body></html>"""
        components.html(tabla_html, height=min(len(res_pr)*30+60,600), scrolling=True)

        st.markdown("---")
        tab_add, tab_edit = st.tabs(["Agregar pedido","Editar / Actualizar"])

        with tab_add:
            with st.form("form_pr_add"):
                c1,c2,c3 = st.columns(3)
                with c1:
                    a_req=st.text_input("Solicitante *")
                    a_sol=st.text_input("N° Solicitud")
                    a_tit=st.text_input("Título *")
                    a_q=st.number_input("Q codes",min_value=0,value=0)
                with c2:
                    a_fec=st.text_input("Fecha solicitud",placeholder="2026-07-XX")
                    a_fap=st.text_input("Fecha aprobación",placeholder="2026-07-XX")
                    a_fpr=st.text_input("Fecha PR",placeholder="2026-07-XX")
                    a_npr=st.text_input("N° PR",placeholder="PR 26XXXXX")
                with c3:
                    a_comp=st.text_input("Comprador")
                    a_est=st.selectbox("Estado",["In progress","Pending","In transit","Delivered","Cancelled"])
                    a_ult=st.text_input("Última consulta",placeholder="2026-07-XX")
                    a_obs=st.text_area("Observaciones",height=68)
                if st.form_submit_button("Agregar",type="primary",use_container_width=True):
                    if a_tit and a_req:
                        run("INSERT INTO purchase_requests (solicitud,titulo,solicitante,fecha_solicitud,fecha_aprobacion,fecha_pr,numero_pr,comprador,estado,q_codes_n,observaciones,ultima_consulta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (a_sol,a_tit,a_req,a_fec,a_fap,a_fpr,a_npr,a_comp,a_est,a_q,a_obs,a_ult))
                        st.success("Pedido agregado"); st.rerun()
                    else: st.error("Solicitante y título son obligatorios")

        with tab_edit:
            if len(pr_df) > 0:
                pr_opts = [f"{r.get('solicitud','?')} — {str(r.get('titulo',''))[:40]}" for _,r in pr_df.iterrows()]
                pr_idx = st.selectbox("Seleccionar pedido",range(len(pr_opts)),format_func=lambda i:pr_opts[i],label_visibility="collapsed",key="pr_edit_sel")
                pr_sel = pr_df.iloc[pr_idx]
                with st.form("form_pr_edit"):
                    c1,c2,c3 = st.columns(3)
                    with c1:
                        e_comp=st.text_input("Comprador",value=str(pr_sel.get("comprador","") or ""))
                        e_npr=st.text_input("N° PR",value=str(pr_sel.get("numero_pr","") or ""))
                        e_fpr=st.text_input("Fecha PR",value=fd(pr_sel.get("fecha_pr")))
                    with c2:
                        estados_pr=["In progress","Pending","In transit","Delivered","Cancelled"]
                        idx_e=estados_pr.index(pr_sel["estado"]) if pr_sel["estado"] in estados_pr else 0
                        e_est=st.selectbox("Estado",estados_pr,index=idx_e)
                        e_ult=st.text_input("Última consulta",value=fd(pr_sel.get("ultima_consulta")))
                    with c3:
                        e_obs=st.text_area("Observaciones",value=str(pr_sel.get("observaciones","") or ""),height=100)
                    c_sv,c_dl = st.columns([3,1])
                    with c_sv:
                        if st.form_submit_button("Guardar",type="primary",use_container_width=True):
                            run("UPDATE purchase_requests SET comprador=?,numero_pr=?,fecha_pr=?,estado=?,ultima_consulta=?,observaciones=? WHERE id=?",
                                (e_comp,e_npr,e_fpr,e_est,e_ult,e_obs,int(pr_sel["id"])))
                            st.success("Guardado"); st.rerun()
                    with c_dl:
                        if st.form_submit_button("Eliminar"):
                            run("DELETE FROM purchase_requests WHERE id=?",(int(pr_sel["id"]),))
                            st.rerun()
    else:
        st.info("Sin pedidos. Cargá un Excel arriba o agregá uno manualmente.")


# ══════════════════════════════════════════════════════════════════════════════
# REPUESTOS Q
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "repuestos":
    st.markdown("## Repuestos Q — POSCO Argentina")

    c1,c2,c3 = st.columns([3,2,2])
    with c1: busq_q = st.text_input(" ",placeholder="Buscar Q code, descripción...",label_visibility="collapsed",key="q_busq")
    with c2:
        cat_q = ["Todos"]+sorted(qdf("SELECT DISTINCT categoria_compra FROM repuestos_q WHERE categoria_compra IS NOT NULL ORDER BY categoria_compra")["categoria_compra"].tolist())
        cat_sel = st.selectbox(" ",cat_q,label_visibility="collapsed",key="q_cat")
    with c3:
        tipo_q_opts = ["Todos"]+sorted(qdf("SELECT DISTINCT tipo_repuesto FROM repuestos_q WHERE tipo_repuesto IS NOT NULL ORDER BY tipo_repuesto")["tipo_repuesto"].dropna().tolist())
        tipo_q_sel = st.selectbox(" ",tipo_q_opts,label_visibility="collapsed",key="q_tipo")

    where_q=[]; params_q=[]
    if busq_q:
        where_q.append("(codigo_q LIKE ? OR descripcion LIKE ? OR fabricante LIKE ? OR numero_parte LIKE ?)")
        params_q += [f"%{busq_q}%"]*4
    if cat_sel!="Todos": where_q.append("categoria_compra=?"); params_q.append(cat_sel)
    if tipo_q_sel!="Todos": where_q.append("tipo_repuesto=?"); params_q.append(tipo_q_sel)

    sql_q = "SELECT codigo_q,descripcion,fabricante,numero_parte,unidad,tipo_repuesto,categoria_compra,status FROM repuestos_q"
    if where_q: sql_q+=" WHERE "+" AND ".join(where_q)
    sql_q += " ORDER BY codigo_q LIMIT 500"
    q_df = qdf(sql_q, tuple(params_q))

    st.caption(f"{len(q_df)} Q codes encontrados de {qdf('SELECT COUNT(*) n FROM repuestos_q').iloc[0]['n']:,} totales")
    st.dataframe(q_df.rename(columns={"codigo_q":"Q Code","descripcion":"Descripción","fabricante":"Fabricante",
        "numero_parte":"N° Parte","unidad":"Ud","tipo_repuesto":"Tipo","categoria_compra":"Categoría","status":"Estado"}),
        use_container_width=True, hide_index=True, height=500)

# ══════════════════════════════════════════════════════════════════════════════
# MAPA
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "mapa":
    st.markdown("## Mapa de Planta")

    AREAS_MAPA = [
        (10,10,"2100","Water Plant","Water Plant","정수 플랜트"),
        (20,10,"2200","Pre-Tratamiento","Pre-Treatment","전처리"),
        (25,10,"2300","Salmuera Pre-Trat.","Brine Pre-Treatment","염수 전처리"),
        (30,10,"2500","Suministro Salmuera","Brine Supply","염수 공급"),
        (10,25,"3100","Apagado de Cal","Lime Preparation","석회 제조"),
        (20,25,"3200","Encalado","Brine Liming","석회 주입"),
        (30,25,"3300","Filtro Prensa","Filter Press","필터프레스"),
        (10,40,"4100","Precipitación Mg/Ca","Mg/Ca Precipitation","Mg/Ca 침전"),
        (20,40,"4200","Intercambio Iónico","Ion Exchange","이온 교환"),
        (25,40,"4300","Carbonatación","Carbonation","탄산화"),
        (30,40,"4400","Filtrado y Lavado","Filtering & Washing","여과 및 세척"),
        (35,40,"4500","Licor Madre","Mother Liquor","모액"),
        (40,40,"4600","Secado y Envasado","Drying & Packaging","건조 및 포장"),
        (10,55,"5100","Reagente H2SO4","Reagent H2SO4","시약 H2SO4"),
        (20,55,"5300","Reagente NaOH","Reagent NaOH","시약 NaOH"),
        (30,55,"5500","Soda Ash","Soda Ash","소다회"),
        (40,55,"6100","Agua de Servicio","Service Water","용수"),
        (50,55,"6500","Aire Comprimido","Compressed Air","압축 공기"),
        (60,10,"0000","Planta Gas","Gas Plant","가스"),
        (70,10,"0000","Power Plant","Power Plant","발전소"),
    ]

    LANG_M = st.session_state.lang
    idx_n = {"es":3,"en":4,"ko":5}.get(LANG_M,3)
    area_sel = st.session_state.get("mapa_area_sel","")

    # Count equipment per area
    eq_por_area = {}
    for row in qdf("SELECT area_codigo, COUNT(*) n FROM equipos WHERE area_codigo IS NOT NULL GROUP BY area_codigo").itertuples(index=False):
        eq_por_area[row[0]] = row[1]

    # Grid of area buttons
    st.markdown("Seleccioná un área para ver sus equipos:")
    rows_grid = {}
    for item in AREAS_MAPA:
        y = item[1]
        if y not in rows_grid: rows_grid[y] = []
        rows_grid[y].append(item)

    for y_coord, items in sorted(rows_grid.items()):
        cols_g = st.columns(len(items))
        for i, item in enumerate(items):
            with cols_g[i]:
                ac = item[2]
                nombre = item[idx_n]
                n_eq = eq_por_area.get(ac,0)
                activo = area_sel == ac
                if st.button(f"{nombre}\n({n_eq} eq.)", key=f"mapa_{ac}_{i}",
                             use_container_width=True,
                             type="primary" if activo else "secondary"):
                    st.session_state["mapa_area_sel"] = ac
                    st.rerun()

    st.markdown("---")

    if area_sel:
        area_info = next((a for a in AREAS_MAPA if a[2]==area_sel), None)
        if area_info:
            nombre_area = area_info[idx_n]
            st.markdown(f"### Área {area_sel} — {nombre_area}")

            try:
                eq_area = qdf("""SELECT tag,tipo_codigo,tipo_descripcion,
                    spec_nombre_equipo,motor_descripcion,mec_fabricante,motor_kw
                    FROM equipos WHERE area_codigo=? ORDER BY tipo_codigo,tag""", (area_sel,))

                if len(eq_area) == 0:
                    st.info("Sin equipos registrados para esta área")
                else:
                    st.caption(f"{len(eq_area)} equipos")
                    st.dataframe(eq_area.rename(columns={"tag":"Tag","tipo_codigo":"Tipo",
                        "tipo_descripcion":"Descripción tipo","spec_nombre_equipo":"Nombre",
                        "motor_descripcion":"Motor","mec_fabricante":"Fabricante","motor_kw":"kW"}),
                        use_container_width=True, hide_index=True)

                    # Click tag -> ficha
                    tag_mapa = st.selectbox("Ver ficha de equipo",
                        ["—"]+eq_area["tag"].tolist(), label_visibility="collapsed", key="mapa_tag_sel")
                    if tag_mapa != "—":
                        st.session_state.pagina = "equipos"
                        st.session_state.eq_busq = tag_mapa
                        st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# PLAN MANTENIMIENTO PREVENTIVO
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "plan":
    st.markdown("## Plan de Mantenimiento Preventivo")

    tab_plan_lista, tab_plan_nueva = st.tabs(["📋 Tareas programadas","➕ Nueva tarea"])

    with tab_plan_lista:
        try:
            plan_df = qdf("""SELECT pm.id,pm.tag_equipo,pm.tipo_tarea,pm.descripcion,
                pm.frecuencia_tipo,pm.frecuencia_valor,pm.ultima_ejecucion,pm.proxima_fecha,
                pm.prioridad,pm.activo,e.spec_nombre_equipo
                FROM plan_mantenimiento pm
                LEFT JOIN equipos e ON pm.tag_equipo=e.tag
                WHERE pm.activo=1
                ORDER BY pm.proxima_fecha""")

            if len(plan_df) == 0:
                st.info("Sin tareas programadas")
            else:
                # Próximas a vencer
                hoy = date.today()
                plan_df["proxima_fecha"] = pd.to_datetime(plan_df["proxima_fecha"], errors="coerce")
                proximas = plan_df[plan_df["proxima_fecha"].notna() &
                                   (plan_df["proxima_fecha"].dt.date <= hoy + timedelta(days=7))]
                if len(proximas) > 0:
                    st.warning(f"⚠️ {len(proximas)} tarea(s) vencen en los próximos 7 días")
                    for _,t2 in proximas.iterrows():
                        st.markdown(f"- **{t2['tag_equipo']}** — {t2['descripcion']} · Fecha: {t2['proxima_fecha'].strftime('%d/%m/%Y')}")
                    st.markdown("---")

                # Tabla completa
                show_plan = plan_df[["tag_equipo","spec_nombre_equipo","tipo_tarea","descripcion",
                                     "frecuencia_valor","frecuencia_tipo","proxima_fecha","prioridad"]].copy()
                show_plan["proxima_fecha"] = show_plan["proxima_fecha"].dt.strftime("%d/%m/%Y")
                st.dataframe(show_plan.rename(columns={
                    "tag_equipo":"Tag","spec_nombre_equipo":"Equipo","tipo_tarea":"Tipo",
                    "descripcion":"Descripción","frecuencia_valor":"Cada","frecuencia_tipo":"Unidad",
                    "proxima_fecha":"Próxima fecha","prioridad":"Prioridad"}),
                    use_container_width=True, hide_index=True)

                # Ejecutar tarea -> crear OT
                if PUEDE_EDITAR:
                    plan_opts = [f"{r['tag_equipo']} — {r['descripcion'][:40]}" for _,r in plan_df.iterrows()]
                    plan_idx = st.selectbox("Ejecutar tarea (crear OT)",
                        range(len(plan_opts)), format_func=lambda i:plan_opts[i],
                        label_visibility="collapsed", key="plan_ej_sel")
                    if st.button("Crear OT para esta tarea", type="primary"):
                        tarea = plan_df.iloc[plan_idx]
                        fecha_hoy = datetime.now().strftime("%Y-%m-%d")
                        try:
                            n_hoy = qdf("SELECT COUNT(*) n FROM ordenes_trabajo WHERE fecha_inicio LIKE ?",
                                        (f"{date.today().isoformat()}%",))
                            seq = int(n_hoy.iloc[0]["n"]) + 1
                        except: seq = 1
                        num_ot = f"OT-{date.today().strftime('%Y%m%d')}-{seq:03d}"
                        run("""INSERT INTO ordenes_trabajo
                            (numero_ot,tag_equipo,titulo,descripcion,tipo_tarea,prioridad,
                             estado,fecha_inicio,horas_estimadas,creado_por,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (num_ot, tarea["tag_equipo"],
                             f"PM: {tarea['descripcion'][:60]}",
                             tarea["descripcion"], tarea["tipo_tarea"],
                             tarea.get("prioridad","normal"),"pendiente",
                             fecha_hoy, tarea.get("horas_estimadas",2),
                             UID, fecha_hoy, fecha_hoy))
                        # Update ultima_ejecucion
                        from dateutil.relativedelta import relativedelta
                        try:
                            frec_tipo = tarea.get("frecuencia_tipo","dias")
                            frec_val  = int(tarea.get("frecuencia_valor",30))
                            hoy_d = date.today()
                            if frec_tipo=="dias": prox = hoy_d + timedelta(days=frec_val)
                            elif frec_tipo=="semanas": prox = hoy_d + timedelta(weeks=frec_val)
                            elif frec_tipo=="meses": prox = hoy_d + relativedelta(months=frec_val)
                            else: prox = hoy_d + timedelta(days=frec_val)
                            run("UPDATE plan_mantenimiento SET ultima_ejecucion=?,proxima_fecha=? WHERE id=?",
                                (fecha_hoy, prox.isoformat(), int(tarea["id"])))
                        except: pass
                        st.success(f"OT creada: {num_ot}"); st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")

    with tab_plan_nueva:
        if not PUEDE_EDITAR:
            st.warning("Sin permisos")
        else:
            busq_eq_plan = st.text_input("Buscar equipo *", placeholder="Tag o nombre...", key="plan_eq_busq")
            eq_plan_df = pd.DataFrame()
            if busq_eq_plan and len(busq_eq_plan) >= 2:
                eq_plan_df = qdf("SELECT tag,spec_nombre_equipo FROM equipos WHERE tag LIKE ? OR spec_nombre_equipo LIKE ? LIMIT 20",
                                 (f"%{busq_eq_plan}%",f"%{busq_eq_plan}%"))
            tag_plan = ""
            if len(eq_plan_df) > 0:
                tag_plan_sel = st.selectbox("Equipo",
                    [f"{r['tag']} — {r.get('spec_nombre_equipo','')}" for _,r in eq_plan_df.iterrows()],
                    label_visibility="collapsed", key="plan_eq_sel")
                tag_plan = tag_plan_sel.split(" — ")[0] if tag_plan_sel else ""

            with st.form("form_plan_nueva"):
                c1,c2 = st.columns(2)
                with c1:
                    p_tipo  = st.selectbox("Tipo *",["preventivo","inspeccion","lubricacion","calibracion","limpieza"])
                    p_desc  = st.text_area("Descripción *", height=80)
                    p_freq  = st.number_input("Cada", min_value=1, value=30)
                    p_frect = st.selectbox("Unidad",["dias","semanas","meses"])
                with c2:
                    p_prox  = st.date_input("Próxima fecha", value=date.today()+timedelta(days=30))
                    p_prior = st.selectbox("Prioridad",["normal","alta","critica","baja"])
                    p_horas = st.number_input("Horas estimadas",min_value=0.0,value=2.0,step=0.5)
                    p_esp   = st.selectbox("Especialidad",["Mecánico","Eléctrico","Instrumentación","General"])
                p_proc  = st.text_area("Procedimiento", height=60)

                if st.form_submit_button("Crear tarea preventiva", type="primary", use_container_width=True):
                    if tag_plan and p_desc:
                        run("""INSERT INTO plan_mantenimiento
                            (tag_equipo,tipo_tarea,descripcion,frecuencia_tipo,frecuencia_valor,
                             proxima_fecha,horas_estimadas,especialidad,procedimiento,prioridad,
                             activo,created_by,created_at,updated_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?)
                            ON CONFLICT DO NOTHING""",
                            (tag_plan,p_tipo,p_desc,p_frect,int(p_freq),
                             p_prox.isoformat(),p_horas,p_esp,p_proc or None,p_prior,
                             UID,datetime.now().strftime("%Y-%m-%d"),datetime.now().strftime("%Y-%m-%d")))
                        st.success("Tarea preventiva creada"); st.rerun()
                    else:
                        st.error("Completá el equipo y la descripción")

# ══════════════════════════════════════════════════════════════════════════════
# PARADAS DE PLANTA
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "paradas":
    st.markdown("## Paradas de Planta")

    tab_par_lista, tab_par_nueva = st.tabs(["📋 Historial","➕ Nueva parada"])

    with tab_par_lista:
        try:
            par_df = qdf("SELECT * FROM paradas_planta ORDER BY fecha_inicio DESC")
            if len(par_df) == 0:
                st.info("Sin paradas registradas")
            else:
                c1,c2,c3 = st.columns(3)
                with c1: st.metric("Total",len(par_df))
                with c2: st.metric("En curso",len(par_df[par_df.estado=="en_curso"]))
                with c3:
                    dur_total = pd.to_numeric(par_df.duracion_h, errors="coerce").sum()
                    st.metric("Horas totales",f"{dur_total:.1f} h")

                est_color_p = {"planificada":"🔵","en_curso":"🟡","completada":"✅","cancelada":"⚫"}
                for _,p in par_df.iterrows():
                    ic = est_color_p.get(str(p.get("estado","")),"⚪")
                    with st.expander(f"{ic} {p.get('nombre','')} · {fmt_fecha(p.get('fecha_inicio'))} → {fmt_fecha(p.get('fecha_fin'))}"):
                        c1,c2 = st.columns(2)
                        with c1:
                            st.markdown(f"**Tipo:** {p.get('tipo','—')}")
                            st.markdown(f"**Área:** {p.get('area_codigo','—')}")
                            st.markdown(f"**Duración:** {p.get('duracion_h','—')} h")
                        with c2:
                            st.markdown(f"**Estado:** {p.get('estado','—')}")
                            st.markdown(f"**Descripción:** {p.get('descripcion','—')}")
                        if PUEDE_EDITAR:
                            est_par_opts = ["planificada","en_curso","completada","cancelada"]
                            idx_p = est_par_opts.index(str(p.get("estado","planificada"))) if str(p.get("estado")) in est_par_opts else 0
                            c1,c2 = st.columns(2)
                            with c1: new_est_p = st.selectbox("Estado",est_par_opts,index=idx_p,key=f"par_est_{p['id']}")
                            with c2: new_dur = st.number_input("Duración h",min_value=0.0,value=float(p.get("duracion_h") or 0),key=f"par_dur_{p['id']}")
                            if st.button("Guardar",key=f"par_sv_{p['id']}",type="primary"):
                                run("UPDATE paradas_planta SET estado=?,duracion_h=? WHERE id=?",(new_est_p,new_dur,int(p["id"])))
                                st.success("Guardado"); st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")

    with tab_par_nueva:
        if not PUEDE_EDITAR:
            st.warning("Sin permisos")
        else:
            with st.form("form_parada_nueva"):
                c1,c2 = st.columns(2)
                with c1:
                    par_nom  = st.text_input("Nombre de la parada *")
                    par_tipo = st.selectbox("Tipo",["programada","correctiva","emergencia"])
                    par_area = st.text_input("Área (código)",placeholder="3100, 3200...")
                    par_desc = st.text_area("Descripción",height=80)
                with c2:
                    par_ini  = st.date_input("Fecha inicio", value=date.today())
                    par_fin  = st.date_input("Fecha fin", value=date.today()+timedelta(days=1))
                    par_dur  = st.number_input("Duración estimada (h)",min_value=0.0,value=8.0,step=0.5)
                    par_est  = st.selectbox("Estado",["planificada","en_curso","completada"])

                if st.form_submit_button("Registrar parada",type="primary",use_container_width=True):
                    if par_nom:
                        run("""INSERT INTO paradas_planta
                            (nombre,tipo,area_codigo,fecha_inicio,fecha_fin,duracion_h,descripcion,estado,responsable,created_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (par_nom,par_tipo,par_area or None,
                             par_ini.isoformat(),par_fin.isoformat(),
                             par_dur,par_desc or None,par_est,UID,
                             datetime.now().strftime("%Y-%m-%d")))
                        st.success("Parada registrada"); st.rerun()
                    else:
                        st.error("El nombre es obligatorio")

# ══════════════════════════════════════════════════════════════════════════════
# BUZÓN DE SUGERENCIAS
# ══════════════════════════════════════════════════════════════════════════════
elif pagina == "buzon":
    st.markdown("## Buzón de Sugerencias")

    # Create table if not exists
    try:
        run("""CREATE TABLE IF NOT EXISTS sugerencias (
            id SERIAL PRIMARY KEY,
            titulo TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            area TEXT,
            prioridad TEXT DEFAULT 'normal',
            estado TEXT DEFAULT 'nueva',
            autor_id INTEGER,
            autor_nombre TEXT,
            created_at TEXT DEFAULT NOW(),
            respuesta TEXT,
            respondido_por TEXT
        )""" if USE_PG else """CREATE TABLE IF NOT EXISTS sugerencias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo TEXT NOT NULL,
            descripcion TEXT NOT NULL,
            area TEXT,
            prioridad TEXT DEFAULT 'normal',
            estado TEXT DEFAULT 'nueva',
            autor_id INTEGER,
            autor_nombre TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            respuesta TEXT,
            respondido_por TEXT
        )""")
    except: pass

    tab_ver, tab_nueva_sug = st.tabs(["📬 Sugerencias","✏️ Nueva sugerencia"])

    with tab_nueva_sug:
        with st.form("form_sug"):
            sug_tit  = st.text_input("Título *")
            sug_area = st.text_input("Área / Sector",placeholder="Eléctrico, Mecánico, General...")
            sug_pri  = st.selectbox("Prioridad",["normal","alta","baja"])
            sug_desc = st.text_area("Descripción / Sugerencia *",height=120)
            if st.form_submit_button("Enviar sugerencia",type="primary",use_container_width=True):
                if sug_tit and sug_desc:
                    run("INSERT INTO sugerencias (titulo,descripcion,area,prioridad,estado,autor_id,autor_nombre) VALUES (?,?,?,?,?,?,?)",
                        (sug_tit,sug_desc,sug_area or None,sug_pri,"nueva",UID,f"{NOMBRE} {APELLIDO}"))
                    st.success("✅ Sugerencia enviada. ¡Gracias!"); st.rerun()
                else:
                    st.error("Completá título y descripción")

    with tab_ver:
        try:
            sug_df = qdf("SELECT * FROM sugerencias ORDER BY created_at DESC")
            if len(sug_df) == 0:
                st.info("Sin sugerencias aún")
            else:
                c1,c2,c3 = st.columns(3)
                with c1: st.metric("Total",len(sug_df))
                with c2: st.metric("Nuevas",len(sug_df[sug_df.estado=="nueva"]))
                with c3: st.metric("Resueltas",len(sug_df[sug_df.estado=="resuelta"]))

                est_ic = {"nueva":"🆕","en_revision":"🔍","resuelta":"✅","descartada":"❌"}
                for _,s in sug_df.iterrows():
                    ic = est_ic.get(str(s.get("estado","")),"📬")
                    with st.expander(f"{ic} {s.get('titulo','')} — {s.get('autor_nombre','')} · {fmt_fecha(s.get('created_at'))}"):
                        c1,c2 = st.columns(2)
                        with c1:
                            st.markdown(f"**Área:** {s.get('area','—')}")
                            st.markdown(f"**Prioridad:** {s.get('prioridad','—')}")
                        with c2:
                            st.markdown(f"**Estado:** {s.get('estado','—')}")
                        st.markdown(f"**Descripción:**\n\n{s.get('descripcion','')}")
                        if s.get("respuesta"):
                            st.info(f"**Respuesta:** {s.get('respuesta')} — {s.get('respondido_por','')}")
                        if PUEDE_ADMIN:
                            st.markdown("---")
                            c1,c2 = st.columns(2)
                            with c1:
                                est_sug_opts = ["nueva","en_revision","resuelta","descartada"]
                                idx_s = est_sug_opts.index(str(s.get("estado","nueva"))) if str(s.get("estado")) in est_sug_opts else 0
                                new_est_s = st.selectbox("Estado",est_sug_opts,index=idx_s,key=f"sug_est_{s['id']}")
                            with c2:
                                new_resp = st.text_input("Respuesta",value=str(s.get("respuesta","") or ""),key=f"sug_resp_{s['id']}")
                            if st.button("Guardar",key=f"sug_sv_{s['id']}",type="primary"):
                                run("UPDATE sugerencias SET estado=?,respuesta=?,respondido_por=? WHERE id=?",
                                    (new_est_s,new_resp,f"{NOMBRE} {APELLIDO}",int(s["id"])))
                                st.success("Guardado"); st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")

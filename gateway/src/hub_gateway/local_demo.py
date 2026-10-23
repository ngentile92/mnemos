"""Demo memory for screenshots, videos and talks (0.12.2): a fictional person, Ana Ribeiro, with ~150 notes in two
spaces over 14 months. Nothing real: `MNEMOS_DEMO=1` points Mnemos at ~/Mnemos-demo (seeded on first use) and never
touches ~/Mnemos. `mnemos-local demo [--home DIR]` seeds a folder by hand.

Every person, company and project here is invented."""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import random
import sqlite3
from pathlib import Path

OWNER = "Ana"
START = dt.datetime(2025, 8, 4, 9, 0)

PERSONAL = [
    "Ana vive en Lisboa, en Alfama, con su pareja Marco y su perra Lola",
    "Marco es arquitecto y está diseñando la casa de Sintra",
    "Ana y Marco compraron un terreno en Sintra para construir una casa con huerta",
    "Lola es una galga adoptada; le encanta correr por la playa de Carcavelos",
    "Ana corre los domingos con su amiga Inés por Belém",
    "Inés es médica en el Hospital de Santa María y amiga de Ana desde la universidad",
    "Ana e Inés se entrenan para el maratón de Lisboa de octubre",
    "Ana terminó la media maratón de Lisboa en 1 h 52 min",
    "Ana aprende cerámica los jueves en el taller de Rosa",
    "Rosa expone sus piezas en la galería Ocre de Lisboa",
    "Ana hizo su primera tetera en el taller de Rosa",
    "A Ana le encanta el café de especialidad; su favorito es el de Fábrica Coffee Roasters",
    "Ana toca el piano y practica jazz con su vecino Tomás",
    "Tomás es contrabajista y toca en el Hot Clube de Lisboa",
    "Ana y Tomás preparan un concierto pequeño para la fiesta del barrio en Alfama",
    "Ana planea un viaje a Japón con Marco en abril: Tokio, Kioto y Naoshima",
    "En Kioto Ana quiere visitar talleres de cerámica raku",
    "Marco quiere ver la arquitectura de Tadao Ando en Naoshima",
    "Ana prefiere viajar en tren y con poco equipaje",
    "La hermana de Ana, Sofía, vive en Oporto con su hija Clara",
    "Clara, la sobrina de Ana, cumple 7 años en mayo y le encantan los dinosaurios",
    "Ana visita a Sofía en Oporto una vez al mes",
    "La madre de Ana, Helena, vive en Coimbra y cultiva olivos",
    "Ana ayuda a Helena con la cosecha de aceitunas cada noviembre en Coimbra",
    "Ana es vegetariana desde 2019",
    "Ana cocina los viernes recetas de Ottolenghi con Marco",
    "Ana lee novelas de Saramago y ahora está con Ensayo sobre la ceguera",
    "Ana tiene un club de lectura mensual con Inés y Rosa",
    "El club de lectura eligió Pessoa para el próximo mes",
    "Ana prefiere respuestas cortas y directas, sin relleno",
    "Ana quiere aprender japonés básico antes del viaje a Japón",
    "Ana estudia japonés con la app Duolingo y una profesora, Keiko",
    "Keiko es profesora de japonés en Lisboa y le recomendó a Ana visitar Kanazawa",
    "Ana y Marco adoptaron a Lola en la protectora Animais de Rua",
    "Ana dona cada mes a la protectora Animais de Rua",
    "Ana hace yoga los martes en un estudio de Graça",
    "Marco y Ana celebran su aniversario el 12 de junio",
    "Para el aniversario Marco reservó una cena en el restaurante Prado de Lisboa",
    "Ana tiene alergia a los frutos secos",
    "Ana quiere plantar limoneros en la huerta de Sintra",
    "Helena le regaló a Ana semillas de tomate de Coimbra para la huerta de Sintra",
    "Ana va en bicicleta eléctrica al trabajo por la ribera del Tajo",
    "Ana sueña con hacer el Camino de Santiago portugués con Inés",
    "Ana grabó un disco casero de jazz con Tomás",
    "Ana guarda sus fotos de viajes en un álbum compartido con Sofía",
]
WORK = [
    "Ana es jefa de producto en Northwind, una empresa de logística con sede en Lisboa",
    "Ana lidera el proyecto Atlas en Northwind: rutas de reparto optimizadas",
    "Atlas usa Postgres, BigQuery y dbt",
    "Lucía Ferreira es la CTO de Northwind y revisa Atlas cada semana",
    "Lucía decidió usar dbt para los modelos de datos de Atlas",
    "Diego Santos mantiene el pipeline de BigQuery de Atlas",
    "Diego y Ana construyen el dashboard de Atlas en React",
    "Northwind lanza Atlas a todos los clientes en marzo",
    "Ana prefiere revisar los PR por la mañana, antes de las reuniones",
    "El proyecto Orion de Northwind migra la plataforma a Kubernetes",
    "Diego y Lucía planifican Orion con el equipo de plataforma",
    "Ana documenta Atlas en Notion",
    "Atlas integra Stripe para la facturación de Northwind",
    "Bruno Costa es el diseñador de producto de Atlas",
    "Bruno diseñó el nuevo mapa de rutas de Atlas en Figma",
    "Ana y Bruno hacen tests con repartidores cada dos semanas",
    "Los repartidores pidieron que Atlas funcione sin conexión",
    "Atlas guarda las rutas en el móvil para trabajar sin conexión",
    "Marta Lopes es la responsable de ventas de Northwind",
    "Marta consiguió a FreshBox como primer cliente grande de Atlas",
    "FreshBox reparte comida en Lisboa y Oporto",
    "FreshBox quiere que Atlas calcule rutas con ventanas horarias",
    "Ana negocia con FreshBox el piloto de Atlas en Oporto",
    "El piloto de FreshBox en Oporto empieza en enero",
    "Lucía quiere medir el ahorro de combustible de Atlas en el piloto de FreshBox",
    "Atlas redujo un 18 % los kilómetros en el piloto de FreshBox",
    "Ana presentó los resultados de Atlas en la conferencia Web Summit de Lisboa",
    "Diego propuso usar OR-Tools para el optimizador de rutas de Atlas",
    "El optimizador de Atlas usa OR-Tools de Google",
    "Ana prefiere decisiones escritas: cada decisión de Atlas tiene un documento en Notion",
    "Northwind hace la planificación trimestral con OKRs",
    "El OKR de Ana este trimestre: 5 clientes usando Atlas",
    "Orion depende de que Atlas pase a Kubernetes primero",
    "Pedro Alves es el ingeniero de plataforma que lidera Orion",
    "Pedro y Diego migran el pipeline de Atlas a Kubernetes",
    "Northwind usa GitHub y Linear para el trabajo de ingeniería",
    "Ana escribe las historias de usuario de Atlas en Linear",
    "Ana tiene una reunión 1:1 con Lucía los lunes",
    "Lucía es mentora de Ana en liderazgo de producto",
    "Ana quiere llegar a directora de producto en Northwind",
    "Northwind abrirá una oficina en Oporto el próximo año",
    "Ana podría liderar el equipo de producto de la oficina de Oporto",
    "Marta y Ana preparan la demo de Atlas para el cliente CargoSul",
    "CargoSul es una empresa de transporte de Faro",
    "CargoSul firmó el contrato de Atlas en febrero",
    "Atlas ya tiene 3 clientes: FreshBox, CargoSul y Lisbon Bikes",
    "Lisbon Bikes usa Atlas para repartir con bicicletas de carga",
    "Ana quiere añadir rutas para bicicletas de carga en Atlas",
    "Bruno hizo un estudio de usabilidad con repartidores de Lisbon Bikes",
    "Diego mejoró el tiempo de cálculo de rutas de Atlas de 40 a 6 segundos",
    "Atlas tiene alertas en Grafana para el tiempo de cálculo de rutas",
    "Pedro configuró Grafana y Prometheus para Orion",
    "Lucía aprobó el presupuesto de Orion para el segundo trimestre",
    "Ana prefiere presentaciones con pocas diapositivas y una demo en vivo",
    "Ana usa Mnemos para recordar decisiones de producto entre reuniones",
    "Northwind celebra la cena de equipo de fin de año en el Time Out Market de Lisboa",
    "Inés le recomendó a Ana una fisioterapeuta para la espalda por las horas de escritorio",
    "Ana y Diego corren juntos los miércoles después del trabajo",
    "Diego también se entrena para el maratón de Lisboa",
    "Marco diseñó la nueva oficina de Northwind en Oporto",
    "Sofía trabaja en FreshBox en Oporto y presentó a Ana al equipo de FreshBox",
    "Rosa hizo las tazas de cerámica de la oficina de Northwind",
    "Tomás toca jazz en la fiesta de fin de año de Northwind",
]
# several facts about one thing, phrased the way people actually tell an assistant
EXTRA = [
    ("personal", "Ana prefiere el café solo, sin azúcar"),
    ("personal", "Lola tiene revisión en el veterinario de Alfama en septiembre"),
    ("personal", "Marco terminó los planos de la casa de Sintra"),
    ("personal", "Las obras de la casa de Sintra empiezan en primavera"),
    ("personal", "Ana y Marco eligieron azulejos portugueses azules para la cocina de Sintra"),
    ("personal", "Inés se muda a Oporto en verano"),
    ("personal", "Ana compró los billetes del tren bala de Tokio a Kioto"),
    ("personal", "Keiko le enseñó a Ana a escribir hiragana"),
    ("personal", "Tomás y Ana tocaron en el Hot Clube de Lisboa por primera vez"),
    ("personal", "Clara le pidió a Ana un dinosaurio de cerámica"),
    ("personal", "Ana hizo un dinosaurio de cerámica para Clara en el taller de Rosa"),
    ("personal", "Helena viene a Lisboa en Navidad"),
    ("personal", "Ana cocina bacalhau vegetariano en Navidad para Helena"),
    ("work", "Atlas pasó la auditoría de seguridad de CargoSul"),
    ("work", "Diego lidera la guardia de Atlas los fines de semana"),
    ("work", "Bruno propuso un modo oscuro para la app de repartidores de Atlas"),
    ("work", "FreshBox amplió Atlas a Lisboa después del piloto en Oporto"),
    ("work", "Lucía presentó Orion al consejo de Northwind"),
    ("work", "Pedro terminó la migración de Atlas a Kubernetes"),
    ("work", "Orion pasa a producción en junio"),
    ("work", "Ana contrató a Joana como analista de datos para Atlas"),
    ("work", "Joana construye los informes de ahorro de Atlas en BigQuery"),
    ("work", "Joana y Diego mantienen los modelos de dbt de Atlas"),
    ("work", "Marta abre conversaciones con una cadena de supermercados de Madrid para Atlas"),
    ("work", "Atlas se presenta en Madrid en la feria de logística SIL"),
    ("work", "Ana viaja a Madrid con Marta para la feria SIL"),
    ("work", "La oficina de Northwind en Oporto abre en septiembre"),
    ("work", "Ana prefiere que los informes semanales lleguen los viernes"),
    ("work", "Lucía confirmó a Ana como directora de producto de Northwind"),
    ("personal", "Ana celebró su ascenso a directora de producto con Marco en el restaurante Prado"),
    ("personal", "Ana e Inés terminaron el maratón de Lisboa juntas"),
    ("personal", "Diego, Ana e Inés corrieron juntos el maratón de Lisboa"),
    ("personal", "Ana quiere correr el maratón de Tokio algún día"),
    ("work", "Bruno diseña la identidad visual de Orion"),
    ("work", "Northwind usa Mnemos en el equipo de producto para recordar decisiones"),
    ("personal", "Ana empezó un huerto en el balcón de Alfama mientras se construye Sintra"),
    ("personal", "Rosa invitó a Ana a exponer dos piezas en la galería Ocre"),
    ("personal", "Ana expuso una tetera y un cuenco raku en la galería Ocre"),
    ("personal", "Marco y Ana planean mudarse a Sintra el próximo otoño"),
    ("personal", "Sofía y Clara pasan el verano en la casa de Helena en Coimbra"),
]


KINDS = {
    "person": "Ana Marco Lola Inés Rosa Tomás Sofía Clara Helena Keiko Lucía Ferreira Diego Santos Bruno Costa Marta Lopes "
              "Pedro Alves Joana Saramago Pessoa Ottolenghi Tadao Ando".split(),
    "org": ["Northwind", "FreshBox", "CargoSul", "Lisbon Bikes", "Animais de Rua", "Hot Clube de Lisboa", "Fábrica Coffee Roasters",
            "Web Summit", "Hospital de Santa María", "Time Out Market", "Prado", "Ocre", "SIL"],
    "project": ["Atlas", "Orion", "Mnemos", "OKRs"],
    "tool": ["Postgres", "BigQuery", "React", "Notion", "Stripe", "Figma", "OR-Tools", "Google", "GitHub", "Linear", "Grafana",
             "Prometheus", "Kubernetes", "Duolingo"],
    "place": ["Lisboa", "Alfama", "Sintra", "Carcavelos", "Belém", "Oporto", "Coimbra", "Japón", "Tokio", "Kioto", "Naoshima",
              "Kanazawa", "Graça", "Faro", "Madrid", "Tajo", "Santiago"],
}


def notes() -> list[tuple[str, str, dt.datetime]]:
    """(space, text, when): the story unfolds over 14 months, so 'How it grew' tells it in order."""
    rnd = random.Random(7)
    seq = [("personal", t) for t in PERSONAL] + [("work", t) for t in WORK]
    rnd.shuffle(seq)
    seq += EXTRA                                   # later chapters come last
    span = 14 * 30 * 24 * 3600
    out = []
    for i, (sp, t) in enumerate(seq):
        when = START + dt.timedelta(seconds=int(span * (i / len(seq)) ** 1.15) + rnd.randint(0, 36000))
        out.append((sp, t, when))
    return out


def seed(home: Path) -> int:
    """Create `home` with the demo notes (notes store, dated). Idempotent: an existing demo is left as it is."""
    from . import local
    home = Path(home)
    if (home / "contexts.yaml").exists():
        return 0
    prev = os.environ.get("MNEMOS_STORE")
    os.environ["MNEMOS_STORE"] = "notes"           # dated rows: the time-lapse needs real dates
    try:
        local.init_home(home, ["personal", "work"], OWNER)
    finally:
        if prev is None:
            os.environ.pop("MNEMOS_STORE", None)
        else:
            os.environ["MNEMOS_STORE"] = prev
    cy = home / "contexts.yaml"
    cy.write_text(cy.read_text() + "bridges:\n- from: personal\n  to: work\n- from: work\n  to: personal\n")
    servers = local.build_all(home, 0)
    when: dict[str, dt.datetime] = {}

    async def go() -> None:
        for sp, text, t in notes():
            loc = servers[sp].mnemos_local
            ds = loc["scope"].write_id(target="context", project=None)[1]
            r = await loc["store"].remember(text, ds, metadata={"source_app": "demo", "hub_context": sp})
            when[r["items"][0]["id"]] = t
            texts[r["items"][0]["id"]] = (sp, text)
    texts: dict[str, tuple[str, str]] = {}
    asyncio.run(go())
    # the kinds a local model would have found (so the demo is coloured without one), and every note marked as
    # read so a running Ollama does not re-read the demo in the background
    from . import local_graph
    kind = {local_graph.norm(n): k for k, ns in KINDS.items() for n in ns}
    for sp in ("personal", "work"):
        mine = [(i, t) for i, (s_, t) in texts.items() if s_ == sp]
        rows = []
        for name, k in kind.items():
            first = next((i for i, t in mine if name in local_graph.norm(t)), None)
            if first:
                rows.append((first, name, k))
        every = [{"id": i, "text": t} for i, (_s, t) in texts.items()]  # bridges: each space reads both
        local_graph.get(home, sp).store(every, [], rows)
    for db in (home / "data" / "notes").glob("*.sqlite"):  # backdate: this is our own demo store
        with sqlite3.connect(db) as c:
            for nid, t in when.items():
                iso = t.isoformat(timespec="seconds")
                c.execute("UPDATE data SET created_at=?, updated_at=? WHERE id=?", (iso, iso, nid))
    (home / "token").write_text(os.urandom(12).hex())
    os.chmod(home / "token", 0o600)
    return len(when)


def demo_home() -> Path:
    return Path.home() / "Mnemos-demo"
